"""
Reconciling two sources that record the same real-world sales or expenses but share no id (a POS and an M-Pesa
statement, a till book and a bank statement). Records are paired one-to-one: same amount, about the same moment.

Nothing is merged on its own. Matching only SUGGESTS (`suggestions`); the owner confirms (`merge`), which marks the
paired records of the second source as duplicates so every total counts them once, and can undo it (`unmerge`).
Records with no partner stay counted and are reported (`merged_pairs`): money in one source and not the other is
exactly what an owner wants to see.
"""
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, time, timedelta
from itertools import combinations

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.core import BusinessEvent as E, Connector

WINDOW = timedelta(minutes=15)          # how far apart two records of one sale may be (a payment follows the sale)
MIN_MATCHES, MIN_SHARE = 5, 0.4         # enough pairs, and a big enough share of the smaller source, to suggest a merge
ENTITIES = ("sale", "expense")
_ROW_CAP = 50_000
_EXAMPLES = 5

Row = tuple[str, datetime, float]       # id, timestamp, amount


def _counted_rows(db: Session, connector_id: str, entity: str, skip: frozenset[str] | set[str] = frozenset()) -> list[Row]:
    q = (db.query(E.id, E.timestamp, E.amount)
         .filter(E.connector_id == connector_id, E.entity == entity, E.validation_status == "validated", E.amount.isnot(None))
         .order_by(E.timestamp).limit(_ROW_CAP))
    return [(i, t, round(float(a), 2)) for i, t, a in q if i not in skip]


def _dates_only(rows: list[Row]) -> bool:
    return bool(rows) and all(t.time() == time.min for _, t, _ in rows)


def pair_rows(keep: list[Row], other: list[Row]) -> list[tuple[str, str]]:
    """One-to-one pairs (keep id, other id) with the same amount at about the same moment: within WINDOW, or on the
    same day when either source records dates only. Nearest first; a record is never paired twice, so two real sales
    of the same amount at the same minute need two partners."""
    daily = _dates_only(keep) or _dates_only(other)
    by_amount: dict[float, list[Row]] = defaultdict(list)
    for r in keep:
        by_amount[r[2]].append(r)                                   # keep is ordered by time
    taken: set[str] = set()
    pairs = []
    for oid, ot, amount in other:
        cands = by_amount.get(amount, [])
        times = [c[1] for c in cands]
        near = (lambda t: t.date() == ot.date()) if daily else (lambda t: abs(t - ot) <= WINDOW)
        i = bisect_left(times, ot)
        reachable = []
        for j in range(i - 1, -1, -1):                              # leftwards while still close
            if not near(times[j]):
                break
            reachable.append(j)
        for j in range(i, len(cands)):                              # rightwards while still close
            if not near(times[j]):
                break
            reachable.append(j)
        free = [j for j in reachable if cands[j][0] not in taken]
        if free:
            best = min(free, key=lambda j: abs(times[j] - ot))
            taken.add(cands[best][0])
            pairs.append((cands[best][0], oid))
    return pairs


def _detail(db: Session, connector_id: str) -> float:
    """How much each record says about where and what (branch, product, customer), 0 to 3: the richer source is the
    better one to keep as the main record, because attribution depends on it."""
    n, loc, prod, cust = db.query(func.count(E.id), func.count(E.location), func.count(E.product_id), func.count(E.customer_id)) \
        .filter(E.connector_id == connector_id).one()
    return (loc + prod + cust) / n if n else 0.0


def suggestions(db: Session, tenant_id: str) -> list[dict]:
    """Pairs of sources that look like the same records, not yet merged, each with the source to keep."""
    sources = db.query(Connector).filter(Connector.tenant_id == tenant_id).order_by(Connector.created_at).all()
    detail = {c.id: _detail(db, c.id) for c in sources}
    out = []
    for a, b in combinations(sources, 2):
        if a.merge_into == b.id or b.merge_into == a.id:
            continue
        keep, other = (a, b) if (detail[a.id], 0) >= (detail[b.id], 0) else (b, a)
        for entity in ENTITIES:
            kr, orows = _counted_rows(db, keep.id, entity), _counted_rows(db, other.id, entity)
            smaller = min(len(kr), len(orows))
            pairs = pair_rows(kr, orows)
            if len(pairs) >= MIN_MATCHES and len(pairs) / smaller >= MIN_SHARE:
                out.append({"entity": entity, "matched": len(pairs), "share": round(len(pairs) / smaller, 2),
                            "window": "the same day" if _dates_only(kr) or _dates_only(orows) else f"{WINDOW.seconds // 60} minutes",
                            "keep": {"id": keep.id, "name": keep.name, "records": len(kr)},
                            "other": {"id": other.id, "name": other.name, "records": len(orows)}})
    return out


def _summary(rows: list[Row]) -> dict:
    return {"count": len(rows), "amount": round(sum(a for _, _, a in rows), 2),
            "recent": [{"timestamp": t.isoformat(), "amount": a} for _, t, a in rows[-_EXAMPLES:][::-1]]}


def merged_pairs(db: Session, tenant_id: str) -> list[dict]:
    """For every confirmed merge: how many records are counted once, and what exists in only one of the two sources."""
    out = []
    for other in db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.merge_into.isnot(None)):
        keep = db.get(Connector, other.merge_into)
        used = {d for (d,) in db.query(E.duplicate_of).filter(E.connector_id == other.id, E.duplicate_of.isnot(None))}
        for entity in ENTITIES:
            only_keep, only_other = _counted_rows(db, keep.id, entity, used), _counted_rows(db, other.id, entity)
            both = db.query(func.count(E.id)).filter(E.connector_id == other.id, E.entity == entity, E.validation_status == "duplicate",
                                                     text("metadata_json::jsonb ->> 'merged_by' = 'rule'")).scalar()
            if both or only_keep or only_other:
                out.append({"entity": entity, "counted_once": both,
                            "keep": {"id": keep.id, "name": keep.name, "only_here": _summary(only_keep)},
                            "other": {"id": other.id, "name": other.name, "only_here": _summary(only_other)}})
    return out


def apply_rule(db: Session, other: Connector) -> int:
    """Pair this source's still-counted records with its main source's unpaired records and mark the paired ones as
    duplicates. Safe to repeat: only records not yet paired are considered."""
    if not other.merge_into:
        return 0
    used = {d for (d,) in db.query(E.duplicate_of).filter(E.connector_id == other.id, E.duplicate_of.isnot(None))}
    marked = 0
    for entity in ENTITIES:
        partner = {o: k for k, o in pair_rows(_counted_rows(db, other.merge_into, entity, used), _counted_rows(db, other.id, entity))}
        for ev in db.query(E).filter(E.id.in_(list(partner))):
            ev.validation_status, ev.duplicate_of = "duplicate", partner[ev.id]
            ev.metadata_json = {**(ev.metadata_json or {}), "merged_by": "rule"}
        marked += len(partner)
    db.flush()
    return marked


def reapply(db: Session, connector_ids: set[str]) -> None:
    """New records arrived in these sources: pair them with their counterparts under every confirmed merge involving
    them, in whichever order the two sources were loaded."""
    rules = db.query(Connector).filter(Connector.merge_into.isnot(None),
                                       Connector.id.in_(connector_ids) | Connector.merge_into.in_(connector_ids)).all()
    for other in rules:
        apply_rule(db, other)
    if rules:
        db.commit()


def merge(db: Session, other: Connector, keep: Connector) -> int:
    """Count `other`'s records once with `keep`'s. Returns how many pairs were found."""
    other.merge_into = keep.id
    marked = apply_rule(db, other)
    db.commit()
    return marked


def unmerge(db: Session, other: Connector) -> int:
    """Undo a merge: every record it hid counts again. Records hidden for another reason (a shared id) stay hidden."""
    restored = db.execute(text(
        "UPDATE business_events SET validation_status = 'validated', duplicate_of = NULL, "
        "metadata_json = (metadata_json::jsonb - 'merged_by')::json "
        "WHERE connector_id = :c AND validation_status = 'duplicate' AND metadata_json::jsonb ->> 'merged_by' = 'rule'"), {"c": other.id}).rowcount
    other.merge_into = None
    db.commit()
    return restored
