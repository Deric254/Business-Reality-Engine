"""Real sheets are messy: day/month dates, totals rows, blank rows, 'KES 1,500'. None of it may silently corrupt the
data or throw the whole sheet away."""
import io

import pandas as pd
import pytest
from fastapi import HTTPException

from app.connectors.file_connector import ColumnMapping, add_row_ids, dataframe_to_normalized_events, with_default_id

MAP = ColumnMapping(entity="sale", event_type="sale.created", timestamp_col="Date", amount_col="Amount")


def _parse(csv: str):
    df = add_row_ids(pd.read_csv(io.StringIO(csv)))
    return dataframe_to_normalized_events(df, with_default_id(df, MAP), "t", "c", "s")


def _days(parsed):
    return [e["timestamp"].date().isoformat() for e in parsed.events]


def test_day_month_dates_are_read_day_first():
    assert _days(_parse("Date,Amount\n03/09/2026,1\n04/09/2026,2\n25/09/2026,3\n")) == ["2026-09-03", "2026-09-04", "2026-09-25"]
    assert _days(_parse("Date,Amount\n3/9/26 14:30,1\n")) == ["2026-09-03"]


def test_a_column_that_proves_month_first_is_read_month_first():
    assert _days(_parse("Date,Amount\n09/03/2026,1\n09/25/2026,2\n")) == ["2026-09-03", "2026-09-25"]


def test_iso_dates_are_untouched_by_the_day_first_rule():
    assert _days(_parse("Date,Amount\n2026-03-09 10:00,1\n")) == ["2026-03-09"]


def test_blank_rows_are_ignored_and_stray_rows_are_skipped_not_fatal():
    p = _parse("Date,Amount\n2026-09-03,100\n,\n2026-09-04,KES 2,500\n2026-09-05,-\nTOTAL,2600\nnote: closed Sunday,\n2026-09-06,7\n".replace("KES 2,500", '"KES 2,500"'))
    assert [e["amount"] for e in p.events] == [100.0, 2500.0, None, 7.0]
    assert p.skipped == ["Row 6: 'TOTAL' is not a date", "Row 7: 'note: closed Sunday' is not a date"]


def test_a_wrong_mapping_still_fails_loudly():
    with pytest.raises(HTTPException) as e:
        _parse("Date,Amount\nMeru,1\nMaua,2\n2026-09-03,3\n")
    assert e.value.status_code == 400 and "mapping is probably wrong" in e.value.detail and "Meru" in e.value.detail
    with pytest.raises(HTTPException):
        _parse("Date,Amount\n2026-09-03,twelve\n")                                  # the only row is bad


def test_rows_are_numbered_even_when_the_parser_left_a_text_index():
    df = pd.DataFrame({"Date": ["2026-09-03", "TOTAL"], "Amount": [1, 2], "__row_id": ["a", "b"]}, index=["x", "y"])
    p = dataframe_to_normalized_events(df, MAP, "t", "c", "s")
    assert len(p.events) == 1 and p.skipped == ["Row 3: 'TOTAL' is not a date"]


def test_a_column_that_mixes_times_with_and_without_a_timezone_is_read():
    p = _parse("Date,Amount\n2026-09-03 22:30:00+00:00,1\n2026-09-04 10:00,2\n03/09/2026 08:15,3\n")
    assert [(e["timestamp"].replace(tzinfo=None).isoformat(), e["timestamp"].tzinfo is None) for e in p.events] == [
        ("2026-09-03T22:30:00", False), ("2026-09-04T10:00:00", True), ("2026-09-03T08:15:00", True)]
