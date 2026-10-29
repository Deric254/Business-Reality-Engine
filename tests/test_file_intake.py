"""A file is understood, not just taken: empty rows and columns are ignored and counted, a column is judged by the cells that
have something in them, a file with no date can be dated as a whole, and the owner is told what was found."""
import datetime as dt
import io

import pandas as pd
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.connectors.file_connector import ColumnMapping, add_row_ids, dataframe_to_normalized_events, parse_file_to_dataframe
from app.connectors.inspect import describe, suggest_mapping
from app.core.db import engine
from tests.test_block_c import make_owner, mk

MESSY = "Date,Item,,Amount,\n2026-10-01,Sugar,,500,\n,,,,\n2026-10-02,Oil,,300,\n,,,,\n,,,,\nnot a date,Rice,,200,\n"
MAP = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Amount", "product_id_col": "Item"}


def parse(text_, ext=".csv"):
    return parse_file_to_dataframe(text_.encode(), ext)


# ---- empty rows and columns ------------------------------------------------------------------------------------------

def test_empty_columns_are_dropped_and_counted_and_empty_rows_are_counted_but_keep_their_place():
    df = parse(MESSY)
    assert list(df.columns) == ["Date", "Item", "Amount"]
    assert df.attrs["quality"] == {"blank_rows": 3, "blank_columns": ["Unnamed: 2", "Unnamed: 4"]}
    assert len(df) == 6                                   # empty rows stay, so row numbers still match the sheet


def test_a_date_column_is_found_even_when_most_rows_are_empty():
    m = suggest_mapping(parse(MESSY))
    assert (m["timestamp_col"], m["amount_col"], m["product_id_col"]) == ("Date", "Amount", "Item")


def test_empty_rows_are_ignored_and_reported_and_a_bad_row_keeps_the_number_the_sheet_shows():
    df = add_row_ids(parse(MESSY))
    parsed = dataframe_to_normalized_events(df, ColumnMapping(**MAP), "t", "c", "s")
    assert len(parsed.events) == 2 and parsed.blank_rows == 3
    assert parsed.skipped == ["Row 7: 'not a date' is not a date"]     # the sheet's own numbering: header 1, Sugar 2, empty 3, Oil 4, empty 5 and 6, then this


def test_a_file_with_nothing_in_it_is_refused_plainly():
    with pytest.raises(Exception) as e:
        parse("Date,Amount\n,\n,\n")
    assert "no data rows" in str(getattr(e.value, "detail", e.value))


def test_the_ai_and_the_preview_never_see_empty_rows():
    info = describe(parse(MESSY))
    assert info["row_count"] == 3 and all(any(v for v in r.values()) for r in info["sample"])
    assert info["columns"] == ["Date", "Item", "Amount"]


# ---- what the owner is told ------------------------------------------------------------------------------------------

def test_the_report_says_what_was_ignored_what_could_not_be_read_and_what_is_missing():
    q = describe(parse(MESSY))["quality"]
    assert q["blank_rows"] == 3 and q["blank_columns"] == ["Unnamed: 2", "Unnamed: 4"]
    assert q["unreadable_dates"] == 1 and q["unreadable_amounts"] == 0 and q["rows_without_amount"] == 0 and q["kind"] == "transactions"


def test_a_column_with_data_but_no_name_is_listed_so_the_owner_can_use_it():
    q = describe(parse("Date,Amount,\n2026-10-01,500,Sugar\n2026-10-02,300,Oil\n"))["quality"]
    assert q["unnamed_columns"] == ["Unnamed: 2"] and q["blank_columns"] == []


@pytest.mark.parametrize("text_,kind", [
    ("Date,Item,Amount\n2026-10-01,Sugar,500\n", "transactions"),
    ("Item,Amount,Customer\nSugar,500,Wanjiru\n", "partial"),         # money but no date
    ("Date,Item,Customer\n2026-10-01,Sugar,Wanjiru\n", "partial"),    # a date but no money
    ("Product,Stock,Supplier\nSugar,40,A\nOil,12,B\n", "list"),      # neither: a list, not sales or expenses
])
def test_the_file_is_classified_so_the_screen_can_say_what_it_is_good_for(text_, kind):
    assert describe(parse(text_))["quality"]["kind"] == kind


def test_a_stock_count_is_never_guessed_to_be_money_but_a_lone_oddly_named_money_column_is_offered_as_a_guess():
    stock = describe(parse("Product,Stock,Supplier\nSugar,40,A\nOil,12,B\n"))
    assert "amount_col" not in stock["suggested_mapping"] and stock["guessed"] == []
    odd = describe(parse("Date,Item,Takings\n2026-10-01,Sugar,\"1,500\"\n2026-10-02,Oil,900\n"))
    assert odd["suggested_mapping"]["amount_col"] == "Takings" and odd["guessed"] == ["amount_col"]


def test_a_plain_number_column_is_not_mistaken_for_dates():
    m = suggest_mapping(parse("Item,Qty,Total\nSugar,2,500\nOil,3,300\n"))
    assert "timestamp_col" not in m


def test_a_column_of_dates_with_no_header_is_found_and_marked_as_a_guess():
    info = describe(parse(",Item,Amount\n2026-10-01,Sugar,500\n2026-10-02,Oil,300\n"))
    assert info["suggested_mapping"]["timestamp_col"] == "Unnamed: 0" and "timestamp_col" in info["guessed"]


# ---- a file with no date ---------------------------------------------------------------------------------------------

def test_a_mapping_needs_a_date_column_or_one_date_for_the_whole_file():
    with pytest.raises(ValidationError, match="Say when these happened"):
        ColumnMapping(entity="sale", event_type="sale.created", amount_col="Amount")
    with pytest.raises(ValidationError, match="real date"):
        ColumnMapping(entity="sale", event_type="sale.created", amount_col="Amount", default_date="31/02/2026")
    assert ColumnMapping(entity="sale", event_type="sale.created", amount_col="Amount", default_date="2026-10-05").default_date == "2026-10-05"


def test_every_row_of_an_undated_file_is_dated_that_day_at_midday():
    df = add_row_ids(parse("Item,Amount\nSugar,500\nOil,300\n"))
    m = ColumnMapping(entity="sale", event_type="sale.created", amount_col="Amount", product_id_col="Item", default_date="2026-10-05")
    parsed = dataframe_to_normalized_events(df, m, "t", "c", "s")
    assert [e["timestamp"] for e in parsed.events] == [dt.datetime(2026, 10, 5, 12, 0)] * 2 and len(parsed.events) == 2


def test_an_undated_file_is_imported_through_the_api_once_and_a_repeat_changes_nothing(client):
    t, h = make_owner(client, "undated")
    cfg = {"entity": "sale", "event_type": "sale.created", "amount_col": "Amount", "product_id_col": "Item", "default_date": "2026-10-05"}
    cid = mk(client, t, h, connector_type="file", config=cfg).json()["id"]
    csv = b"Item,Amount\nSugar,500\nOil,300\n"
    first = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("a.csv", csv, "text/csv")}, headers=h)
    assert first.status_code == 200 and first.json()["rows_processed"] == 2, first.text
    again = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("a.csv", csv, "text/csv")}, headers=h)
    assert all(r["duplicate"] for r in again.json()["results"])
    with engine.connect() as c:
        assert c.execute(text("select count(*) from business_events where connector_id = :c"), {"c": cid}).scalar() == 2


def test_a_source_with_no_date_at_all_is_refused_when_it_is_created(client):
    t, h = make_owner(client, "nodate")
    r = mk(client, t, h, connector_type="file", config={"entity": "sale", "event_type": "sale.created", "amount_col": "Amount"})
    assert r.status_code == 400 and "Say when these happened" in r.text


# ---- through the API, the way the wizard does it --------------------------------------------------------------------

def test_inspection_returns_the_quality_report_and_the_guesses(client):
    t, h = make_owner(client, "inspectq")
    r = client.post("/api/connectors/inspect-file", data={"tenant_id": t}, files={"file": ("m.csv", MESSY.encode(), "text/csv")}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == 3 and body["quality"]["blank_rows"] == 3 and body["guessed"] == []
    assert body["suggested_mapping"]["timestamp_col"] == "Date"


def test_the_upload_report_mentions_ignored_empty_rows_only_when_there_were_some(client):
    t, h = make_owner(client, "uploadq")
    cid = mk(client, t, h, connector_type="file", config=MAP).json()["id"]
    r = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("m.csv", MESSY.encode(), "text/csv")}, headers=h)
    assert r.status_code == 200 and r.json()["blank_rows"] == 3 and r.json()["skipped"] == 1
    clean = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("n.csv", b"Date,Item,Amount\n2026-11-01,Tea,100\n", "text/csv")}, headers=h)
    assert clean.status_code == 200 and "blank_rows" not in clean.json()
