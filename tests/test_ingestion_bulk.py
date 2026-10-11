import datetime as dt
import threading
import uuid

import pytest
from fastapi import HTTPException

from app.core.db import SessionLocal
from app.core.ingestion import NormalizedEvent, ingest_events_bulk, ingest_normalized_event
from app.core.entity_resolution import resolve_entity
from app.models.core import Tenant, Connector, BusinessEvent, ResolvedEntity, EntityAlias
from tests.conftest import _make_tenant


@pytest.fixture()
def env():
    db = SessionLocal()
    t = Tenant(name=f"ing-{uuid.uuid4().hex[:6]}"); db.add(t); db.commit()
    c = Connector(tenant_id=t.id, name="c", connector_type="webhook", config={}); db.add(c); db.commit()
    yield db, t.id, c.id
    db.close()


def ev(t, c, eid, customer=None, source="pos", amount=10.0):
    return NormalizedEvent(tenant_id=t, connector_id=c, source=source, entity="sale", entity_id=eid, event_type="sale",
                           timestamp=dt.datetime.utcnow() - dt.timedelta(hours=1), amount=amount, customer_id=customer)


def count(db, model, **f):
    return db.query(model).filter_by(**f).count()


def test_results_in_input_order_and_idempotent_resend(env):
    db, t, c = env
    batch = [ev(t, c, f"e{i}", f"Cust {i % 3}") for i in range(10)]
    first = ingest_events_bulk(db, batch)
    assert [r["duplicate"] for r in first] == [False] * 10
    again = ingest_events_bulk(db, batch)
    assert [r["duplicate"] for r in again] == [True] * 10
    assert [r["id"] for r in again] == [r["id"] for r in first]
    assert count(db, BusinessEvent, connector_id=c) == 10


def test_duplicate_ids_inside_one_batch(env):
    db, t, c = env
    res = ingest_events_bulk(db, [ev(t, c, "same"), ev(t, c, "same"), ev(t, c, "other")])
    assert [r["duplicate"] for r in res] == [False, True, False]
    assert res[0]["id"] == res[1]["id"]
    assert count(db, BusinessEvent, connector_id=c) == 2


def test_events_without_entity_id_are_always_inserted(env):
    db, t, c = env
    ingest_events_bulk(db, [ev(t, c, None), ev(t, c, None)])
    assert count(db, BusinessEvent, connector_id=c) == 2


def test_exact_normalized_match_autolinks_and_repeats_share_entity(env):
    db, t, c = env
    r = ingest_events_bulk(db, [ev(t, c, "1", "John Mwangi"), ev(t, c, "2", "john mwangi"), ev(t, c, "3", "John Mwangi")])
    ids = {db.get(BusinessEvent, x["id"]).resolved_customer_id for x in r}
    assert len(ids) == 1
    assert count(db, ResolvedEntity, tenant_id=t, entity_type="customer") == 1


def test_initials_and_similar_surnames_are_suggested_never_linked(env):
    db, t, c = env
    ingest_events_bulk(db, [ev(t, c, "1", "John Mwangi")])
    res = ingest_events_bulk(db, [ev(t, c, "2", "J. Mwangi"), ev(t, c, "3", "Jane Mwangi")])
    john = db.query(ResolvedEntity).filter_by(tenant_id=t, canonical_name="John Mwangi").one()
    assert count(db, ResolvedEntity, tenant_id=t, entity_type="customer") == 3   # nothing auto-merged
    for r in res:
        flags = r["entity_review_flags"]
        assert flags and flags[0]["suggestions"][0]["resolved_entity_id"] == john.id


def test_suggestions_see_entities_created_earlier_in_the_same_batch(env):
    db, t, c = env
    res = ingest_events_bulk(db, [ev(t, c, "1", "John Mwangi"), ev(t, c, "2", "J. Mwangi")])
    first = db.get(BusinessEvent, res[0]["id"]).resolved_customer_id
    sugg = res[1]["entity_review_flags"][0]["suggestions"]
    assert sugg and sugg[0]["resolved_entity_id"] == first


def test_same_name_in_two_tenants_never_shares_an_entity(env):
    db, t, c = env
    t2 = Tenant(name="other"); db.add(t2); db.commit()
    c2 = Connector(tenant_id=t2.id, name="c", connector_type="webhook", config={}); db.add(c2); db.commit()
    a = ingest_normalized_event(db, ev(t, c, "1", "Shared Name"))
    b = ingest_normalized_event(db, ev(t2.id, c2.id, "1", "Shared Name"))
    ea, eb = db.get(BusinessEvent, a["id"]), db.get(BusinessEvent, b["id"])
    assert ea.resolved_customer_id != eb.resolved_customer_id


def test_connector_tenant_mismatch_rejects_whole_batch_before_writing(env):
    db, t, c = env
    other = Tenant(name="evil"); db.add(other); db.commit()
    with pytest.raises(HTTPException) as e:
        ingest_events_bulk(db, [ev(t, c, "ok-1", "Alice"), ev(other.id, c, "bad-1", "Bob")])
    assert e.value.status_code == 403
    assert count(db, BusinessEvent, connector_id=c) == 0
    assert count(db, ResolvedEntity, tenant_id=t) == 0


def test_unknown_and_inactive_connector(env):
    db, t, c = env
    with pytest.raises(HTTPException) as e:
        ingest_events_bulk(db, [ev(t, str(uuid.uuid4()), "x")])
    assert e.value.status_code == 404
    db.query(Connector).filter_by(id=c).update({"is_active": False}); db.commit()
    with pytest.raises(HTTPException) as e:
        ingest_events_bulk(db, [ev(t, c, "x")])
    assert e.value.status_code == 409


def test_validation_flags_but_still_stores(env):
    db, t, c = env
    r = ingest_normalized_event(db, ev(t, c, "neg", amount=-5))
    assert r["validation_status"] == "failed" and r["errors"]
    assert db.get(Connector, c).last_sync_status == "partial"


def test_tz_aware_timestamp_accepted(env):
    db, t, c = env
    e = ev(t, c, "tz"); e.timestamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    assert ingest_normalized_event(db, e)["duplicate"] is False


def test_concurrent_senders_converge_on_one_entity_and_one_row_per_event(env):
    _, t, c = env
    batch = [ev(t, c, f"e{i}", f"Brand New {i % 25}") for i in range(100)]
    errors = []

    def worker():
        s = SessionLocal()
        try:
            ingest_events_bulk(s, batch)
        except Exception as exc:  # noqa
            errors.append(repr(exc))
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [x.start() for x in threads]; [x.join() for x in threads]
    assert not errors, errors
    s = SessionLocal()
    try:
        assert count(s, BusinessEvent, connector_id=c) == 100
        assert count(s, ResolvedEntity, tenant_id=t, entity_type="customer") == 25
        assert count(s, EntityAlias, tenant_id=t, entity_type="customer") == 25
    finally:
        s.close()


def test_resolve_entity_wrapper_and_attributes(env):
    db, t, c = env
    r = resolve_entity(db, t, "supplier", "erp", "  Acme Ltd ", {"tier": "gold"})
    assert not r.matched_existing
    assert db.get(ResolvedEntity, r.resolved_entity_id).attributes == {"tier": "gold"}
    assert resolve_entity(db, t, "supplier", "erp", "acme ltd").matched_existing
    with pytest.raises(ValueError):
        resolve_entity(db, t, "supplier", "erp", "   ")


def test_merge_moves_aliases_and_events(env):
    from app.core.entity_resolution import merge_entities
    db, t, c = env
    r = ingest_events_bulk(db, [ev(t, c, "1", "John Mwangi"), ev(t, c, "2", "J. Mwangi")])
    keep = db.get(BusinessEvent, r[0]["id"]).resolved_customer_id
    gone = db.get(BusinessEvent, r[1]["id"]).resolved_customer_id
    merge_entities(db, t, keep, gone)
    db.expire_all()
    assert db.get(BusinessEvent, r[1]["id"]).resolved_customer_id == keep
    assert count(db, EntityAlias, entity_id=keep) == 2
    # after merging, the abbreviated form resolves straight to the kept entity
    assert resolve_entity(db, t, "customer", "pos", "J. Mwangi").resolved_entity_id == keep


# ---------- batched Tier-2 equivalence ----------
def test_batched_suggestions_match_per_value_reference():
    """The one-query LATERAL retrieval must return what the per-value query returns."""
    import random
    from app.core import entity_resolution as ER
    from app.core.db import SessionLocal
    rnd = random.Random(3)
    first = ["john", "jane", "joan", "mary", "peter", "paul", "grace", "gracie", "ann", "anne"]
    last = ["mwangi", "mwang", "otieno", "kamau", "njoroge", "kiprop", "mutua"]
    db = SessionLocal()
    try:
        t = _make_tenant("eq")
        base = list({f"{rnd.choice(first)} {rnd.choice(last)}" for _ in range(60)})
        ER.resolve_entities_bulk(db, [(t, "customer", "s", n) for n in base]); db.commit()
        probes = ["john mwangi", "J. Mwangi", "jon mwangi", "grace kamau", "g kamau", "anny otieno", "peter", "zzz qqq", "x"]
        norms = [(p, ER._normalize(p)) for p in probes]
        batched = ER._db_suggestions_bulk(db, t, "customer", norms)
        for raw, norm in norms:
            ref = ER._db_suggestions(db, t, "customer", raw, norm) if norm else []
            key = lambda s: s["resolved_entity_id"]
            assert sorted(ref, key=key) == sorted(batched.get(raw, []), key=key), raw
    finally:
        db.close()


def test_in_call_suggestions_match_brute_force(env):
    """The C-speed prefilter for 'earlier values in the same batch' must not drop or add suggestions."""
    from app.core import entity_resolution as ER
    from app.core.db import SessionLocal
    names = ["john mwangi", "jon mwangi", "j mwangi", "jane mwangi", "mary kamau", "m kamau", "marie kamau", "zed quux"]
    db = SessionLocal()
    try:
        t = _make_tenant("bf")
        res = ER.resolve_entities_bulk(db, [(t, "customer", "s", n) for n in names])
        for i, n in enumerate(names):
            expect = set()
            for j in range(i):
                score, _ = ER._score(n, [names[j]])
                if score >= ER.SUGGESTION_THRESHOLD:
                    expect.add(res[(t, "customer", "s", names[j])].resolved_entity_id)
            got = {s["resolved_entity_id"] for s in res[(t, "customer", "s", n)].suggestions}
            assert got <= expect and len(got) == min(3, len(expect)), (n, got, expect)
    finally:
        db.rollback(); db.close()
