"""Businesses keep data in the shape that suits them. The importer reads it as it is: report titles above the header,
separate money-in / money-out columns (M-Pesa, bank), quantity x unit price, a Type column."""
import io

import openpyxl
import pandas as pd

from app.connectors.file_connector import ColumnMapping, add_row_ids, dataframe_to_normalized_events, parse_file_to_dataframe, with_default_id
from app.connectors.inspect import describe, suggest_mapping
from tests.test_block_c import make_owner, mk
from tests.test_url_connector import _FakeAI

MPESA = ('Receipt No.,Completion Time,Details,Paid In,Withdrawn,Balance\n'
         'SHK1,03/09/2026 10:00,Customer Payment from A,"1,500",,5000\n'
         'SHK2,04/09/2026 11:00,Pay Bill to supplier,,300,4700\n'
         'SHK3,05/09/2026 12:00,Customer Payment from B,"2,000",,6700\n')


def _events(csv, mapping):
    df = add_row_ids(parse_file_to_dataframe(csv.encode(), ".csv"))
    return dataframe_to_normalized_events(df, with_default_id(df, ColumnMapping(**mapping)), "t", "c", "s")


def test_title_rows_above_the_header_are_skipped_and_row_numbers_stay_true():
    csv = "ABC Traders Ltd,,,\nSales report 1 Oct to 31 Oct,,,\n,,,\nDate,Receipt,Amount,Branch\n2026-10-01,R1,100,Meru\nTOTAL,,100,\n"
    df = parse_file_to_dataframe(csv.encode(), ".csv")
    assert list(df.columns) == ["Date", "Receipt", "Amount", "Branch"] and len(df) == 2
    p = _events(csv, {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Amount"})
    assert [e["amount"] for e in p.events] == [100.0] and p.skipped == ["Row 6: 'TOTAL' is not a date"]      # row 6 in the sheet


def test_title_rows_in_an_excel_workbook():
    wb = openpyxl.Workbook(); ws = wb.active
    for row in (["Mama Njeri Shop"], ["Monthly sales"], [], ["Date", "Item", "Total"], ["2026-10-01", "Sugar", 650]):
        ws.append(row)
    buf = io.BytesIO(); wb.save(buf)
    df = parse_file_to_dataframe(buf.getvalue(), ".xlsx")
    assert list(df.columns) == ["Date", "Item", "Total"] and df.iloc[0]["Item"] == "Sugar" and df.attrs["header_row"] == 4


def test_a_normal_file_is_untouched_by_header_detection():
    df = parse_file_to_dataframe(b"Date,Amount\n2026-10-01,5\n", ".csv")
    assert list(df.columns) == ["Date", "Amount"] and df.attrs["header_row"] == 1 and len(df) == 1


def test_mpesa_style_statement_is_mapped_and_read_as_sales_and_expenses():
    df = pd.read_csv(io.StringIO(MPESA))
    m = suggest_mapping(df)
    assert (m["amount_in_col"], m["amount_out_col"], m["timestamp_col"], m["entity_id_col"]) == ("Paid In", "Withdrawn", "Completion Time", "Receipt No.")
    p = _events(MPESA, {k: v for k, v in m.items()} | {"event_type": "sale.created"})
    assert [(e["entity"], e["event_type"], e["amount"], e["timestamp"].day) for e in p.events] == [
        ("sale", "sale.created", 1500.0, 3), ("expense", "expense.created", 300.0, 4), ("sale", "sale.created", 2000.0, 5)]


def test_debit_and_credit_columns_are_money_out_and_in():
    m = suggest_mapping(pd.DataFrame({"Date": ["2026-09-03", "2026-09-04"], "Narration": ["a", "b"], "Debit": [50, 0], "Credit": [0, 80]}))
    assert (m["amount_in_col"], m["amount_out_col"]) == ("Credit", "Debit") and "amount_col" not in m


def test_quantity_times_unit_price_when_there_is_no_total():
    csv = "Date,Item,Qty,Unit Price\n2026-09-03,Sugar,3,650\n2026-09-04,Oil,2,\n"
    m = suggest_mapping(pd.read_csv(io.StringIO(csv)))
    assert m["price_col"] == "Unit Price" and "amount_col" not in m
    p = _events(csv, m | {"event_type": "sale.created"})
    assert [e["amount"] for e in p.events] == [1950.0, None]                  # no price: amount unknown, never invented


def test_type_column_decides_sale_or_expense_per_row():
    csv = "Date,Type,Amount\n2026-09-03,Sale,100\n2026-09-04,Purchase,40\n2026-09-05,Cash sale,60\n"
    m = suggest_mapping(pd.read_csv(io.StringIO(csv)))
    assert m["type_col"] == "Type"
    assert [e["entity"] for e in _events(csv, m | {"event_type": "sale.created"}).events] == ["sale", "expense", "sale"]


def test_a_row_with_no_money_either_way_is_skipped_not_invented():
    csv = "Date,In,Out\n2026-09-03,100,\n2026-09-04,,\n2026-09-05,,30\n"
    p = _events(csv, {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_in_col": "In", "amount_out_col": "Out"})
    assert [(e["entity"], e["amount"]) for e in p.events] == [("sale", 100.0), ("expense", 30.0)] and p.skipped == ["Row 3: no money in or out"]


def test_ai_can_propose_the_new_shapes_and_bad_picks_are_dropped():
    df = pd.read_csv(io.StringIO("Tarehe,Pesa Ndani,Pesa Nje,Jina\n03/09/2026,100,,Juma\n04/09/2026,,40,Duka\n"))
    ai = _FakeAI('{"mapping": {"timestamp_col": "Tarehe", "amount_in_col": "Pesa Ndani", "amount_out_col": "Pesa Nje", "price_col": "Jina"}, "reasons": {}}')
    m = describe(df, ai)["suggested_mapping"]
    assert (m["timestamp_col"], m["amount_in_col"], m["amount_out_col"]) == ("Tarehe", "Pesa Ndani", "Pesa Nje") and "price_col" not in m   # a name is not a price


def test_end_to_end_statement_upload_counts_money_in_as_sales_and_out_as_expenses(client):
    t, h = make_owner(client, "anyfmt")
    cid = mk(client, t, h, connector_type="file", config={"entity": "sale", "event_type": "sale.created", "timestamp_col": "Completion Time",
             "amount_in_col": "Paid In", "amount_out_col": "Withdrawn", "entity_id_col": "Receipt No."}).json()["id"]
    import datetime as dt
    csv = "M-PESA statement for Mama Njeri\n,,,,,\n" + MPESA.replace("03/09/2026", f"{dt.datetime.utcnow() - dt.timedelta(days=3):%d/%m/%Y}") \
        .replace("04/09/2026", f"{dt.datetime.utcnow() - dt.timedelta(days=2):%d/%m/%Y}").replace("05/09/2026", f"{dt.datetime.utcnow() - dt.timedelta(days=1):%d/%m/%Y}")
    r = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("m.csv", csv.encode(), "text/csv")}, headers=h)
    assert r.status_code == 200 and r.json()["rows_processed"] == 3 and r.json()["skipped"] == 0, r.text
    k = client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()
    assert k["revenue"]["current"] == 3500 and k["expenses"]["current"] == 300
