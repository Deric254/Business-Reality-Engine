"""M-Pesa: live paybill/till payments (Daraja C2B confirmations) and statement files."""
import datetime as dt

import pytest
from sqlalchemy import text

from app.core.db import engine

from tests.test_block_c import make_owner, mk

# The shape Safaricom's Daraja API posts to a C2B confirmation URL.
C2B = {"TransactionType": "Pay Bill", "TransID": "RKTQDM7W6S", "TransTime": "20261020123045", "TransAmount": "1500.00",
       "BusinessShortCode": "600638", "BillRefNumber": "INV-77", "InvoiceNumber": "", "OrgAccountBalance": "49197.00",
       "ThirdPartyTransID": "", "MSISDN": "254708374149", "FirstName": "John", "MiddleName": "", "LastName": "Doe"}


def _nairobi(t):
    """Test businesses default to UTC, which hides any mistake about time zones; these tests use a Nairobi business."""
    with engine.begin() as c:
        c.execute(text("update tenants set timezone = 'Africa/Nairobi' where id = :t"), {"t": t})


def _mpesa_connector(client, t, h):
    r = mk(client, t, h, connector_type="webhook", name="Paybill", config={"preset": "mpesa_c2b"})
    assert r.status_code == 200, r.text
    return r.json()


def _stored(connector_id):
    with engine.connect() as c:
        return c.execute(text("select entity, entity_id, amount, timestamp, customer_id from business_events where connector_id = :c order by timestamp"),
                         {"c": connector_id}).all()


def test_a_paybill_confirmation_becomes_a_sale_at_the_time_it_happened(client):
    t, h = make_owner(client, "mpesa1")
    _nairobi(t)
    c = _mpesa_connector(client, t, h)
    assert c["webhook_url"].startswith("/api/webhooks/")
    r = client.post(c["webhook_url"], json=C2B)
    assert r.status_code == 200
    assert r.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}            # the reply Daraja expects
    (row,) = _stored(c["id"])
    assert (row.entity, row.entity_id, float(row.amount), row.customer_id) == ("sale", "RKTQDM7W6S", 1500.0, "254708374149")
    assert row.timestamp == dt.datetime(2026, 10, 20, 9, 30, 45)               # 12:30:45 in Nairobi (UTC+3) is 09:30:45 UTC


def test_a_repeated_confirmation_is_not_counted_twice(client):
    t, h = make_owner(client, "mpesa2")
    _nairobi(t)
    c = _mpesa_connector(client, t, h)
    for _ in range(3):
        assert client.post(c["webhook_url"], json=C2B).status_code == 200      # the same payment delivered again
    assert len(_stored(c["id"])) == 1


def test_unknown_preset_is_refused_and_a_bad_payment_time_is_a_clear_error(client):
    t, h = make_owner(client, "mpesa3")
    assert mk(client, t, h, connector_type="webhook", config={"preset": "nope"}).status_code == 400
    c = _mpesa_connector(client, t, h)
    r = client.post(c["webhook_url"], json={**C2B, "TransTime": "yesterday"})
    assert r.status_code == 400 and "TransTime" in r.json()["detail"]


# ---- a payload time that carries a zone, or no time at all, must land at the right moment ----------------------------

def _generic_webhook(client, t, h, **mapping):
    r = mk(client, t, h, connector_type="webhook", config={"entity": "sale", "event_type": "sale.created", "amount_path": "amount",
                                                           "entity_id_path": "id", **mapping})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_webhook_timestamp_with_a_zone_is_converted_not_reread_as_local_time(client):
    t, h = make_owner(client, "webhookz")
    _nairobi(t)
    c = _generic_webhook(client, t, h, timestamp_path="at")
    client.post(c["webhook_url"], json={"id": "z1", "amount": 10, "at": "2026-10-20T10:00:00Z"})
    client.post(c["webhook_url"], json={"id": "z2", "amount": 10, "at": "2026-10-20T13:00:00+03:00"})
    client.post(c["webhook_url"], json={"id": "z3", "amount": 10, "at": "2026-10-20T13:00:00"})        # no zone: the business's own clock
    rows = {r.entity_id: r.timestamp for r in _stored(c["id"])}
    assert rows["z1"] == rows["z2"] == rows["z3"] == dt.datetime(2026, 10, 20, 10, 0, 0)


def test_a_webhook_with_no_timestamp_is_stamped_with_the_moment_it_arrived(client):
    t, h = make_owner(client, "webhookr")
    _nairobi(t)
    c = _generic_webhook(client, t, h)
    before = dt.datetime.utcnow()
    client.post(c["webhook_url"], json={"id": "r1", "amount": 10})
    after = dt.datetime.utcnow()
    (row,) = _stored(c["id"])
    assert before - dt.timedelta(seconds=5) <= row.timestamp <= after + dt.timedelta(seconds=5)      # not shifted by the business's UTC offset


def test_a_webhook_batch_with_no_timestamps_is_stamped_with_arrival_time_too(client):
    t, h = make_owner(client, "webhookb")
    _nairobi(t)
    c = _generic_webhook(client, t, h)
    before = dt.datetime.utcnow()
    r = client.post(c["webhook_url"] + "/batch", json={"events": [{"id": "b1", "amount": 5}, {"id": "b2", "amount": 6}]})
    assert r.status_code == 200 and r.json()["accepted"] == 2
    for row in _stored(c["id"]):
        assert abs((row.timestamp - before).total_seconds()) < 10


# ---- statement files -----------------------------------------------------------------------------------------------

STATEMENT = (
    "Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance\n"
    "TAO4BUGHNC,{d1} 10:00:00,Customer Transfer of Funds Charge,Completed,,78.00,100.00\n"
    "TAO4BUGHNC,{d1} 10:00:00,Customer Transfer to - JOHN DOE,Completed,,\"5,500.00\",22.00\n"
    "TAO4BUGHNC,{d1} 10:00:00,OverDraft of Credit Party,Completed,570.83,,5578.00\n"
    "TAP6G557Y4,{d2} 11:00:00,Customer Payment from MARY,Completed,\"2,000.00\",,7578.00\n")
MAPPING = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Completion Time", "amount_in_col": "Paid In",
           "amount_out_col": "Withdrawn", "entity_id_col": "Receipt No."}


def test_statement_rows_that_share_a_receipt_number_are_all_kept(client):
    t, h = make_owner(client, "mpesastmt")
    cid = mk(client, t, h, connector_type="file", config=MAPPING).json()["id"]
    d1, d2 = (dt.datetime.utcnow() - dt.timedelta(days=n) for n in (3, 2))
    csv = STATEMENT.format(d1=f"{d1:%Y-%m-%d}", d2=f"{d2:%Y-%m-%d}").encode()
    r = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("m.csv", csv, "text/csv")}, headers=h)
    assert r.status_code == 200 and r.json()["rows_processed"] == 4 and r.json()["skipped"] == 0, r.text
    k = client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()
    assert k["revenue"]["current"] == pytest.approx(2570.83) and k["expenses"]["current"] == pytest.approx(5578.0)
    again = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("m.csv", csv, "text/csv")}, headers=h)
    assert all(x["duplicate"] for x in again.json()["results"])                # uploading the same statement twice changes nothing
    k2 = client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()
    assert k2["revenue"]["current"] == pytest.approx(2570.83) and k2["expenses"]["current"] == pytest.approx(5578.0)


def test_the_withdraw_spelling_some_statements_use_is_recognised():
    import io
    import pandas as pd
    from app.connectors.inspect import suggest_mapping
    df = pd.read_csv(io.StringIO("Receipt No,Completion Time,Details,Transaction Status,Paid in,Withdraw,Balance\n"
                                 "A1,2026-10-01 10:00:00,x,COMPLETED,100.00,0.00,5\nA2,2026-10-02 10:00:00,y,COMPLETED,0.00,40.00,-35\n"))
    m = suggest_mapping(df)
    assert (m["amount_in_col"], m["amount_out_col"]) == ("Paid in", "Withdraw")


# ---- leaving out money that is not a cost of the business -------------------------------------------------------------

LEAVE_OUT_STATEMENT = (
    "Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance\n"
    "R1,{d} 09:00:00,Customer Payment from MARY,Completed,\"3,000.00\",,3000.00\n"
    "R2,{d} 10:00:00,Business Settlement to bank,Completed,,\"2,000.00\",1000.00\n"
    "R3,{d} 11:00:00,Customer Transfer to - OWNER,Completed,,500.00,500.00\n"
    "R4,{d} 12:00:00,Pay Bill to KPLC,Completed,,300.00,200.00\n"
    "R5,{d} 13:00:00,Settlement reversal from bank,Completed,800.00,,1000.00\n")


def _statement_connector(client, t, h, **extra):
    r = mk(client, t, h, connector_type="file", config={**MAPPING, "details_col": "Details", **extra})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload(client, t, h, cid, body):
    return client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("m.csv", body, "text/csv")}, headers=h)


def _csv():
    d = dt.datetime.utcnow() - dt.timedelta(days=2)
    return LEAVE_OUT_STATEMENT.format(d=f"{d:%Y-%m-%d}").encode()


def test_rows_the_owner_asks_to_leave_out_are_not_counted_and_are_reported(client):
    t, h = make_owner(client, "leaveout1")
    cid = _statement_connector(client, t, h, exclude_words=["Settlement", " transfer to ", ""])
    r = _upload(client, t, h, cid, _csv())
    assert r.status_code == 200, r.text
    assert (r.json()["left_out"], r.json()["left_out_total"]) == (2, 2500.0)          # only the two money-OUT rows that match
    assert r.json()["rows_processed"] == 3 and r.json()["skipped"] == 0
    k = client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()
    assert k["expenses"]["current"] == pytest.approx(300.0)                              # only the KPLC bill is a cost
    assert k["revenue"]["current"] == pytest.approx(3800.0)                              # money in is never left out, even if it says "settlement"


def test_nothing_is_left_out_unless_the_owner_lists_words(client):
    t, h = make_owner(client, "leaveout2")
    cid = _statement_connector(client, t, h)
    r = _upload(client, t, h, cid, _csv())
    assert r.status_code == 200 and "left_out" not in r.json() and r.json()["rows_processed"] == 5
    assert client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()["expenses"]["current"] == pytest.approx(2800.0)


def test_leaving_most_rows_out_is_not_mistaken_for_a_wrong_column_mapping(client):
    t, h = make_owner(client, "leaveout3")
    cid = _statement_connector(client, t, h, exclude_words=["settlement", "transfer", "pay bill"])
    r = _upload(client, t, h, cid, _csv())
    assert r.status_code == 200 and r.json()["left_out"] == 3 and r.json()["rows_processed"] == 2


def test_leave_out_words_are_validated(client):
    t, h = make_owner(client, "leaveout4")
    assert mk(client, t, h, connector_type="file", config={**MAPPING, "details_col": "Details", "exclude_words": ["x" * 41]}).status_code == 400
    assert mk(client, t, h, connector_type="file", config={**MAPPING, "details_col": "Details", "exclude_words": [f"w{i}" for i in range(21)]}).status_code == 400


def test_a_statement_shaped_file_suggests_its_details_column():
    import io
    import pandas as pd
    from app.connectors.inspect import suggest_mapping
    df = pd.read_csv(io.StringIO(LEAVE_OUT_STATEMENT.format(d="2026-10-01")))
    m = suggest_mapping(df)
    assert m["details_col"] == "Details" and m.get("product_id_col") is None
