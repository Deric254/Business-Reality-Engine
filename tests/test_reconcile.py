"""Two sources that record the same sales with no shared id (a POS and an M-Pesa statement): detected, confirmed by the
owner, counted once, undoable, and still reconciling as new data arrives."""
import datetime as dt

import pytest

from app.core.reconcile import pair_rows
from tests.conftest import bearer, register
from tests.test_block_c import make_owner, mk

BASE = (dt.datetime.utcnow() - dt.timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
AMOUNTS = [110 + 10 * i for i in range(12)]                      # 12 POS sales, every amount different
EXTRAS = [777, 888, 999]                                         # M-Pesa payments the POS never recorded
POS_CFG = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Time", "amount_col": "Amount", "location_col": "Branch"}
MPESA_CFG = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Completion Time", "amount_in_col": "Paid In", "amount_out_col": "Withdrawn"}
STAMP = "%d/%m/%Y %H:%M:%S"


def _at(i, minutes=0):
    return BASE + dt.timedelta(minutes=45 * i + minutes)


def _pos_csv(rows):                                              # rows: (time, amount)
    return "Time,Amount,Branch\n" + "".join(f"{t:{STAMP}},{a},{'Meru' if i % 2 else 'Maua'}\n" for i, (t, a) in enumerate(rows))


def _mpesa_csv(payments, withdrawals=()):
    return "Completion Time,Paid In,Withdrawn\n" + "".join(f"{t:{STAMP}},{a},\n" for t, a in payments) + "".join(f"{t:{STAMP}},,{a}\n" for t, a in withdrawals)


def _load(client, t, h, name, cfg, csv):
    cid = mk(client, t, h, name=name, connector_type="file", config=cfg).json()["id"]
    assert client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("x.csv", csv.encode(), "text/csv")}, headers=h).status_code == 200
    return cid


def _kpi(client, t, h, which):
    return client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()[which]["current"]


@pytest.fixture()
def shop(client):
    """POS has all 12 sales (10 also paid by M-Pesa two minutes later; 2 were cash). M-Pesa also holds 3 payments the POS missed."""
    t, h = make_owner(client, "rec")
    pos = _load(client, t, h, "POS", POS_CFG, _pos_csv([(_at(i), a) for i, a in enumerate(AMOUNTS)]))
    mp = _load(client, t, h, "M-Pesa", MPESA_CFG,
               _mpesa_csv([(_at(i, 2), a) for i, a in enumerate(AMOUNTS[:10])] + [(_at(20 + i, 5), a) for i, a in enumerate(EXTRAS)], [(_at(30), 300)]))
    return t, h, pos, mp


def test_it_suggests_the_merge_and_keeps_the_source_with_more_detail(client, shop):
    t, h, pos, mp = shop
    assert _kpi(client, t, h, "revenue") == sum(AMOUNTS) + sum(AMOUNTS[:10]) + sum(EXTRAS)                # double counted today
    s = client.get(f"/api/connectors/overlaps?tenant_id={t}", headers=h).json()["suggestions"]
    assert len(s) == 1 and s[0]["entity"] == "sale" and s[0]["matched"] == 10 and s[0]["window"] == "15 minutes"
    assert s[0]["keep"]["id"] == pos and s[0]["other"]["id"] == mp and s[0]["share"] == round(10 / 12, 2)  # POS names the branch


def test_confirming_counts_each_sale_once_and_shows_what_is_in_only_one_source(client, shop):
    t, h, pos, mp = shop
    assert client.post(f"/api/connectors/{mp}/merge-into/{pos}?tenant_id={t}", headers=h).json() == {"counted_once": 10}
    assert _kpi(client, t, h, "revenue") == sum(AMOUNTS) + sum(EXTRAS)
    assert _kpi(client, t, h, "expenses") == 300                                                          # withdrawals untouched
    m = next(x for x in client.get(f"/api/connectors/overlaps?tenant_id={t}", headers=h).json()["merged"] if x["entity"] == "sale")
    assert m["counted_once"] == 10 and client.get(f"/api/connectors/overlaps?tenant_id={t}", headers=h).json()["suggestions"] == []
    assert (m["other"]["only_here"]["count"], m["other"]["only_here"]["amount"]) == (3, float(sum(EXTRAS)))   # payments with no sale recorded
    assert (m["keep"]["only_here"]["count"], m["keep"]["only_here"]["amount"]) == (2, float(sum(AMOUNTS[10:])))  # the two cash sales
    assert {c["id"]: c["merge_into"] for c in client.get(f"/api/connectors/?tenant_id={t}", headers=h).json()}[mp] == pos


def test_records_loaded_later_are_paired_whichever_source_comes_second(client, shop):
    t, h, pos, mp = shop
    client.post(f"/api/connectors/{mp}/merge-into/{pos}?tenant_id={t}", headers=h)
    total = _kpi(client, t, h, "revenue")
    up = lambda cid, csv: client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("x.csv", csv.encode(), "text/csv")}, headers=h)
    up(pos, _pos_csv([(_at(20, 5), 777)]))                      # the POS now records a sale an M-Pesa payment already covered
    assert _kpi(client, t, h, "revenue") == total + 777 - 777
    up(mp, _mpesa_csv([(_at(10, 3), AMOUNTS[10])]))             # M-Pesa now holds the payment for a cash sale the POS has
    assert _kpi(client, t, h, "revenue") == total


def test_undo_gives_every_hidden_record_back(client, shop):
    t, h, pos, mp = shop
    before = _kpi(client, t, h, "revenue")
    client.post(f"/api/connectors/{mp}/merge-into/{pos}?tenant_id={t}", headers=h)
    assert client.delete(f"/api/connectors/{mp}/merge?tenant_id={t}", headers=h).json() == {"restored": 10}
    assert _kpi(client, t, h, "revenue") == before
    assert client.delete(f"/api/connectors/{mp}/merge?tenant_id={t}", headers=h).status_code == 400      # nothing left to undo


def test_undo_does_not_touch_records_hidden_by_a_shared_id(client):
    t, h = make_owner(client, "rec2")
    cfg = {**POS_CFG, "entity_id_col": "Receipt"}
    a = _load(client, t, h, "A", cfg, "Time,Receipt,Amount,Branch\n" + f"{_at(0):{STAMP}},R1,100,Meru\n{_at(1):{STAMP}},R2,200,Meru\n")
    b = _load(client, t, h, "B", cfg, "Time,Receipt,Amount,Branch\n" + f"{_at(0):{STAMP}},R1,100,Meru\n{_at(2):{STAMP}},R9,300,Meru\n")
    assert _kpi(client, t, h, "revenue") == 600                                                           # R1 counted once by its id
    client.post(f"/api/connectors/{b}/merge-into/{a}?tenant_id={t}", headers=h)
    assert client.delete(f"/api/connectors/{b}/merge?tenant_id={t}", headers=h).json() == {"restored": 0}
    assert _kpi(client, t, h, "revenue") == 600


def test_disconnecting_the_main_source_gives_the_other_its_records_back(client, shop):
    t, h, pos, mp = shop
    client.post(f"/api/connectors/{mp}/merge-into/{pos}?tenant_id={t}", headers=h)
    client.delete(f"/api/connectors/{pos}?tenant_id={t}", headers=h)
    assert _kpi(client, t, h, "revenue") == sum(AMOUNTS[:10]) + sum(EXTRAS)                               # all of M-Pesa's sales count again
    assert [c["merge_into"] for c in client.get(f"/api/connectors/?tenant_id={t}", headers=h).json()] == [None]


def test_only_the_owner_merges_and_nonsense_requests_are_refused(client, shop):
    t, h, pos, mp = shop
    _, _, staff = register(client, t, role="staff")
    url = lambda a, b: f"/api/connectors/{a}/merge-into/{b}?tenant_id={t}"
    assert client.post(url(mp, pos), headers=bearer(staff)).status_code == 403
    assert client.post(url(pos, pos), headers=h).status_code == 400
    other_t, other_h = make_owner(client, "rec3")
    assert client.post(f"/api/connectors/{mp}/merge-into/{pos}?tenant_id={other_t}", headers=other_h).status_code == 404
    assert client.post(url(mp, pos), headers=h).status_code == 200
    assert client.post(url(pos, mp), headers=h).status_code == 400                                       # no cycles, no chains


def _row(i, minutes, amount):
    return (f"r{i}", BASE + dt.timedelta(minutes=minutes), amount)


def test_pairing_is_one_to_one_and_nearest_first():
    keep = [_row(1, 0, 100.0), _row(2, 1, 100.0)]
    other = [_row(10, 0, 100.0), _row(11, 1, 100.0), _row(12, 2, 100.0)]
    assert sorted(pair_rows(keep, other)) == [("r1", "r10"), ("r2", "r11")]                              # the third has no partner left


def test_the_window_is_fifteen_minutes_and_amounts_must_match():
    keep = [_row(1, 0, 100.0)]
    assert pair_rows(keep, [_row(9, 15, 100.0)]) == [("r1", "r9")]
    assert pair_rows(keep, [_row(9, 16, 100.0)]) == []
    assert pair_rows(keep, [_row(9, 1, 100.5)]) == []


def test_a_source_with_dates_only_pairs_on_the_same_day():
    day = dt.datetime(2026, 9, 3)
    keep = [("k1", day, 100.0), ("k2", day + dt.timedelta(days=1), 100.0)]
    other = [("o1", day.replace(hour=17, minute=40), 100.0)]
    assert pair_rows(keep, other) == [("k1", "o1")]
