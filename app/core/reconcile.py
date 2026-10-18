"""
Reconciling two sources that record the same real-world sales or expenses but share no id (a POS and an M-Pesa
statement, a till book and a bank statement). Records are paired one-to-one: same amount, within WINDOW of each other.

Nothing is merged on its own. Matching only SUGGESTS (`suggestions`); the owner confirms (`merge`), which marks the
paired records of the second source as 'matched' so every total counts them once, and can undo it (`unmerge`). Records
with no partner stay counted and are reported (`merged_pairs`): money in one source and not the other is exactly what
an owner wants to see.

Everything runs inside the database, set-based, so it holds up with millions of records and several sources:
- pairing is a window-function join, never rows loaded into Python;
- after a merge, new arrivals are paired incrementally, only against records near them in time;
- suggestions look at the last RECENT_DAYS only, are tried inside a transaction that is rolled back, and are cached.
"""
from datetime import datetime, timedelta
from itertools import combinations
from typing import Optional

from sqlalchemy import exists, func, text
from sqlalchemy.orm import Session, aliased

from app.core.cache import cached
from app.models.core import BusinessEvent as E, Connector

WINDOW = timedelta(minutes=15)           # how far apart two records of one sale may be (a payment follows the sale)
MIN_MATCHES, MIN_SHARE = 5, 0.4          # enough pairs, and a big enough share of the smaller source, to suggest a merge
RECENT_DAYS = 60                         # suggestions and the "only in one source" lists look at this much recent history
ENTITIES = ("sale", "expense")
_MAX_SOURCES = 8                         # suggestions compare at most this many sources pairwise
_EXAMPLES = 5
_SUGGESTION_TTL_SECONDS = 300
_WINDOW_S = int(WINDOW.total_seconds())
# Records are grouped into buckets twice the window wide, once as they fall and once shifted by the window: any two
# records closer than the window share a bucket in at least one pass. Within a bucket the i-th record of one source is
# paired with the i-th of the other (same amount, in time order) and the pair is kept only if it really is within WINDOW.
_PASSES = (0, _WINDOW_S)
_BUCKET_S = 2 * _WINDOW_S

# Each pass copies the records to compare into two small temporary tables and ANALYZEs them, so the planner always works
# from true sizes. Against the big events table alone it can only guess (its statistics are stale right after a bulk
# import) and a wrong guess turns pairing into comparing every record with every other.
# Pairing itself is grouping, not joining: both sources' records go into one list, each numbered within (source, amount,
# bucket); records with the same (amount, bucket, number) from the two sources are a pair.
_USED = """
CREATE TEMP TABLE pair_used AS
SELECT duplicate_of AS id FROM business_events WHERE tenant_id = :tenant AND connector_id = :other AND duplicate_of IS NOT NULL
"""
_SIDES = """
CREATE TEMP TABLE pair_side AS
SELECT 'k' AS s, e.id, e.amount, e.timestamp, {bucket} AS b FROM business_events e
WHERE e.tenant_id = :tenant AND e.connector_id = :keep AND e.entity = :entity AND e.validation_status = 'validated' AND e.amount IS NOT NULL {span}
  AND NOT EXISTS (SELECT 1 FROM pair_used u WHERE u.id = e.id)
UNION ALL
SELECT 'o', e.id, e.amount, e.timestamp, {bucket} FROM business_events e
WHERE e.tenant_id = :tenant AND e.connector_id = :other AND e.entity = :entity AND e.validation_status = 'validated' AND e.amount IS NOT NULL {span}
"""
_MATCH = """
WITH ranked AS (SELECT s, id, amount, timestamp, b, row_number() OVER (PARTITION BY s, amount, b ORDER BY timestamp, id) AS rn FROM pair_side),
pairs AS MATERIALIZED (
    SELECT (array_agg(id) FILTER (WHERE s = 'k'))[1] AS keep_id, (array_agg(id) FILTER (WHERE s = 'o'))[1] AS other_id,
           (array_agg(timestamp) FILTER (WHERE s = 'k'))[1] AS keep_at, (array_agg(timestamp) FILTER (WHERE s = 'o'))[1] AS other_at
    FROM ranked GROUP BY amount, b, rn
    HAVING count(*) FILTER (WHERE s = 'k') = 1 AND count(*) FILTER (WHERE s = 'o') = 1)
UPDATE business_events t SET validation_status = 'matched', duplicate_of = pairs.keep_id FROM pairs WHERE t.id = pairs.other_id AND {near}
"""
_WINDOWED = ("floor((extract(epoch from e.timestamp) + :shift) / :bucket)::bigint", "abs(extract(epoch from pairs.keep_at - pairs.other_at)) <= :window")
_DAILY = ("(e.timestamp::date - DATE '2000-01-01')", "true")      # a source with dates only can only be matched by day


def _dates_only(db: Session, tenant_id: str, connector_id: str, entity: str, span: Optional[tuple[datetime, datetime]]) -> bool:
    """Whether a source records dates without times (judged on up to 2,000 of its records)."""
    where = " AND timestamp >= :t0 AND timestamp < :t1" if span else ""
    sql = ("SELECT bool_and(timestamp::time = TIME '00:00') FROM (SELECT timestamp FROM business_events WHERE tenant_id = :t AND connector_id = :c "
           f"AND entity = :e AND validation_status = 'validated'{where} LIMIT 2000) s")
    params = {"t": tenant_id, "c": connector_id, "e": entity, **({"t0": span[0], "t1": span[1]} if span else {})}
    return bool(db.execute(text(sql), params).scalar())


def _both_sides_have_records(db: Session, params: dict) -> bool:
    side = ("EXISTS (SELECT 1 FROM business_events WHERE tenant_id = :tenant AND connector_id = :{c} AND entity = :entity "
            "AND validation_status = 'validated' AND amount IS NOT NULL AND timestamp >= :t0 AND timestamp < :t1)")
    return bool(db.execute(text(f"SELECT {side.format(c='keep')} AND {side.format(c='other')}"), params).scalar())


def _pair(db: Session, tenant_id: str, keep_id: str, other_id: str, entity: str, span: Optional[tuple[datetime, datetime]] = None) -> int:
    """Mark as 'matched' the records of `other` that pair with an unpaired record of `keep`. Returns how many pairs.
    `span` limits both sides to records inside it (widened by WINDOW so pairs on its edge are still found)."""
    daily = _dates_only(db, tenant_id, keep_id, entity, span) or _dates_only(db, tenant_id, other_id, entity, span)
    bucket, near = _DAILY if daily else _WINDOWED
    clause = " AND e.timestamp >= :t0 AND e.timestamp < :t1" if span else ""
    params = {"tenant": tenant_id, "keep": keep_id, "other": other_id, "entity": entity, "bucket": _BUCKET_S, "window": _WINDOW_S,
              **({"t0": span[0] - WINDOW, "t1": span[1] + WINDOW} if span else {})}
    if span and not _both_sides_have_records(db, params):
        return 0                                        # nothing near the new records to pair with: skip the work entirely
    paired = 0
    for shift in ((0,) if daily else _PASSES):
        db.execute(text("DROP TABLE IF EXISTS pair_used, pair_side"))
        db.execute(text(_USED), params)
        db.execute(text("ANALYZE pair_used"))
        db.execute(text(_SIDES.format(bucket=bucket, span=clause)), {**params, "shift": shift})
        db.execute(text("ANALYZE pair_side"))
        paired += db.execute(text(_MATCH.format(near=near)), params).rowcount
    db.execute(text("DROP TABLE IF EXISTS pair_used, pair_side"))
    return paired


def _recent_start(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.utcnow()) - timedelta(days=RECENT_DAYS)


def _recent_records(db: Session, tenant_id: str, connector_id: str, entity: str, since: datetime) -> int:
    return db.query(func.count(E.id)).filter(E.tenant_id == tenant_id, E.connector_id == connector_id, E.entity == entity,
                                             E.validation_status == "validated", E.amount.isnot(None), E.timestamp >= since).scalar()


def _detail(db: Session, tenant_id: str, connector_id: str) -> float:
    """How much each record says about where and what (branch, product, customer), 0 to 3, judged on its latest 5,000
    records: the richer source is the better one to keep as the main record, because attribution depends on it."""
    latest = (db.query(E.location, E.product_id, E.customer_id).filter(E.tenant_id == tenant_id, E.connector_id == connector_id)
              .order_by(E.timestamp.desc()).limit(5000).subquery())
    n, loc, prod, cust = db.query(func.count(), func.count(latest.c.location), func.count(latest.c.product_id), func.count(latest.c.customer_id)).select_from(latest).one()
    return (loc + prod + cust) / n if n else 0.0


def _suggestions(db: Session, tenant_id: str) -> list[dict]:
    since = _recent_start()
    sources = db.query(Connector).filter(Connector.tenant_id == tenant_id).order_by(Connector.created_at).limit(_MAX_SOURCES).all()
    detail = {c.id: _detail(db, tenant_id, c.id) for c in sources}
    out = []
    for a, b in combinations(sources, 2):
        if a.merge_into == b.id or b.merge_into == a.id:
            continue
        keep, other = (a, b) if detail[a.id] >= detail[b.id] else (b, a)
        for entity in ENTITIES:
            kn, on = _recent_records(db, tenant_id, keep.id, entity, since), _recent_records(db, tenant_id, other.id, entity, since)
            if min(kn, on) < MIN_MATCHES:
                continue
            trial = db.begin_nested()                              # try the pairing for real, then take it all back
            matched = _pair(db, tenant_id, keep.id, other.id, entity, (since, datetime.utcnow()))
            trial.rollback()
            if matched >= MIN_MATCHES and matched / min(kn, on) >= MIN_SHARE:
                out.append({"entity": entity, "matched": matched, "share": round(matched / min(kn, on), 2), "window_days": RECENT_DAYS,
                            "window": f"{_WINDOW_S // 60} minutes", "keep": {"id": keep.id, "name": keep.name, "records": kn},
                            "other": {"id": other.id, "name": other.name, "records": on}})
    return out


def _only_here(db: Session, tenant_id: str, connector_id: str, entity: str, since: datetime, unpaired_with: Optional[str] = None) -> dict:
    """Counted records of a source in the recent window; with `unpaired_with`, only those no record of that other
    source was matched to."""
    q = db.query(E.timestamp, E.amount).filter(E.tenant_id == tenant_id, E.connector_id == connector_id, E.entity == entity,
                                               E.validation_status == "validated", E.amount.isnot(None), E.timestamp >= since)
    if unpaired_with:
        partner = aliased(E)
        q = q.filter(~exists().where(partner.duplicate_of == E.id, partner.connector_id == unpaired_with))
    count, amount = q.with_entities(func.count(), func.coalesce(func.sum(E.amount), 0)).one()
    recent = q.order_by(E.timestamp.desc()).limit(_EXAMPLES).all()
    return {"count": count, "amount": float(amount), "recent": [{"timestamp": t.isoformat(), "amount": float(a)} for t, a in recent]}


def _merged_pairs(db: Session, tenant_id: str) -> list[dict]:
    since, out = _recent_start(), []
    for other in db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.merge_into.isnot(None)):
        keep = db.get(Connector, other.merge_into)
        for entity in ENTITIES:
            matched = db.query(func.count(E.id)).filter(E.tenant_id == tenant_id, E.connector_id == other.id, E.entity == entity, E.validation_status == "matched").scalar()
            only_keep, only_other = _only_here(db, tenant_id, keep.id, entity, since, unpaired_with=other.id), _only_here(db, tenant_id, other.id, entity, since)
            if matched or only_keep["count"] or only_other["count"]:
                out.append({"entity": entity, "counted_once": matched, "window_days": RECENT_DAYS,
                            "keep": {"id": keep.id, "name": keep.name, "only_here": only_keep},
                            "other": {"id": other.id, "name": other.name, "only_here": only_other}})
    return out


def overview(db: Session, tenant_id: str) -> dict:
    """Suggested merges and, for confirmed ones, what exists in only one source. Cached briefly; ingestion, merge,
    undo and disconnect drop the tenant's `overlaps:` cache entries."""
    return cached(f"overlaps:{tenant_id}", _SUGGESTION_TTL_SECONDS, lambda: {"suggestions": _suggestions(db, tenant_id), "merged": _merged_pairs(db, tenant_id)})


def apply_rule(db: Session, other: Connector, span: Optional[tuple[datetime, datetime]] = None) -> int:
    """Pair this source's still-counted records with its main source's unpaired records and mark the paired ones
    'matched'. Safe to repeat: only records not yet paired are considered. `span` limits the work to records near
    newly arrived ones."""
    if not other.merge_into:
        return 0
    return sum(_pair(db, other.tenant_id, other.merge_into, other.id, entity, span) for entity in ENTITIES)


def reapply(db: Session, connector_ids: set[str], span: tuple[datetime, datetime]) -> None:
    """New records arrived in these sources, between span[0] and span[1]: pair them with their counterparts under
    every confirmed merge involving them, in whichever order the two sources were loaded."""
    rules = db.query(Connector).filter(Connector.merge_into.isnot(None),
                                       Connector.id.in_(connector_ids) | Connector.merge_into.in_(connector_ids)).all()
    for other in rules:
        apply_rule(db, other, span)
    if rules:
        db.commit()


def merge(db: Session, other: Connector, keep: Connector) -> int:
    """Count `other`'s records once with `keep`'s. Returns how many pairs were found."""
    other.merge_into = keep.id
    paired = apply_rule(db, other)
    db.commit()
    return paired


def unmerge(db: Session, other: Connector) -> int:
    """Undo a merge: every record it hid counts again. Records hidden for another reason (a shared id) stay hidden."""
    restored = db.execute(text("UPDATE business_events SET validation_status = 'validated', duplicate_of = NULL "
                               "WHERE connector_id = :c AND validation_status = 'matched'"), {"c": other.id}).rowcount
    other.merge_into = None
    db.commit()
    return restored
