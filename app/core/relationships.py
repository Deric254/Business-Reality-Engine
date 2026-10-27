"""
Section 6: relationships between business entities, and what drives a change.

Everything is computed from validated events at query time (no stored graph to
drift out of date): which products a customer buys, which customers a product
sells to, which supplier costs concentrate where. Concentration is stated in
plain numbers ("62% of Tea sales come from one customer") because dependence on
one customer, product or place is a business risk in itself.
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import BusinessEvent, ResolvedEntity
from app.core.clock import utcnow

ROLE_COL = {
    "customer": BusinessEvent.resolved_customer_id, "product": BusinessEvent.resolved_product_id,
    "employee": BusinessEvent.resolved_employee_id, "supplier": BusinessEvent.resolved_supplier_id,
}
DIMENSIONS = {**ROLE_COL, "location": BusinessEvent.location}
CONCENTRATION_FLAG = 0.5          # one partner >= 50% of the value
DEPENDENCE_FLAG = 0.25            # entity >= 25% of the tenant total


def _value_entity(entity_type: str) -> str:
    return "expense" if entity_type == "supplier" else "sale"


def _grouped(db, tenant_id, col, base_filters, start, end, limit=5):
    q = (db.query(col, func.coalesce(func.sum(BusinessEvent.amount), 0), func.count(BusinessEvent.id))
         .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.timestamp >= start, BusinessEvent.timestamp < end,
                 BusinessEvent.validation_status == "validated", col.isnot(None), *base_filters)
         .group_by(col).order_by(func.sum(BusinessEvent.amount).desc().nullslast()).limit(limit))
    return [(k, float(v), int(n)) for k, v, n in q]


def _names(db, tenant_id, ids):
    if not ids:
        return {}
    return {r.id: r.canonical_name for r in db.query(ResolvedEntity).filter(
        ResolvedEntity.tenant_id == tenant_id, ResolvedEntity.id.in_(list(ids)))}


def relationships_for(db: Session, tenant_id: str, entity_id: str, days: int = 90,
                      now: Optional[datetime] = None) -> Optional[dict]:
    ent = db.query(ResolvedEntity).filter(ResolvedEntity.id == entity_id, ResolvedEntity.tenant_id == tenant_id).first()
    if not ent or ent.entity_type not in ROLE_COL:
        return None
    now = now or utcnow()
    start = now - timedelta(days=days)
    own = ROLE_COL[ent.entity_type]
    value_entity = _value_entity(ent.entity_type)
    base = [own == entity_id, BusinessEvent.entity == value_entity]
    total_row = (db.query(func.coalesce(func.sum(BusinessEvent.amount), 0), func.count(BusinessEvent.id))
                 .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.timestamp >= start, BusinessEvent.timestamp < now,
                         BusinessEvent.validation_status == "validated", *base).one())
    total, n_events = float(total_row[0]), int(total_row[1])
    tenant_total = float(db.query(func.coalesce(func.sum(BusinessEvent.amount), 0)).filter(
        BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == value_entity, BusinessEvent.timestamp >= start,
        BusinessEvent.timestamp < now, BusinessEvent.validation_status == "validated").scalar() or 0)
    out = {"entity": {"id": ent.id, "type": ent.entity_type, "name": ent.canonical_name}, "period_days": days,
           "measure": f"{value_entity} amount", "total": total, "events": n_events, "linked": {}, "statements": []}
    share = total / tenant_total if tenant_total > 0 else None
    out["share_of_business"] = round(share, 4) if share is not None else None
    label = "sales" if value_entity == "sale" else "expenses"
    if share is not None and share >= DEPENDENCE_FLAG:
        out["statements"].append(f"{ent.canonical_name} accounts for {share*100:.0f}% of all {label} in the last {days} days.")
    for dim, col in DIMENSIONS.items():
        if dim == ent.entity_type:
            continue
        rows = _grouped(db, tenant_id, col, base, start, now)
        names = _names(db, tenant_id, [k for k, _, _ in rows]) if dim != "location" else {}
        items = [{"id": None if dim == "location" else k, "name": k if dim == "location" else names.get(k, "(unknown)"),
                  "amount": v, "events": n, "share": round(v / total, 4) if total > 0 else None} for k, v, n in rows]
        out["linked"][dim] = items
        if items and items[0]["share"] is not None and items[0]["share"] >= CONCENTRATION_FLAG and total > 0:
            out["statements"].append(
                f"{items[0]['share']*100:.0f}% of {ent.canonical_name}'s {label} involve one {dim} ({items[0]['name']}).")
    return out


def change_drivers(db: Session, tenant_id: str, entity: str, start: datetime, end: datetime) -> dict:
    """Which customer/product/location moved the most between the period before [start,end) and the period itself.
    A driver is reported only if it explains >= 50% of the net change, in plain numbers."""
    length = end - start
    prev_start = start - length
    result = {"entity": entity, "drivers": [], "statements": []}
    for dim, col in DIMENSIONS.items():
        if dim == "supplier" and entity == "sale":
            continue
        cur = {k: v for k, v, _ in _grouped(db, tenant_id, col, [BusinessEvent.entity == entity], start, end, limit=200)}
        prev = {k: v for k, v, _ in _grouped(db, tenant_id, col, [BusinessEvent.entity == entity], prev_start, start, limit=200)}
        keys = set(cur) | set(prev)
        if not keys:
            continue
        deltas = {k: cur.get(k, 0.0) - prev.get(k, 0.0) for k in keys}
        net = sum(deltas.values())
        if abs(net) < 1e-9:
            continue
        top = max(deltas, key=lambda k: abs(deltas[k]) if (deltas[k] > 0) == (net > 0) else 0)
        part = deltas[top] / net
        if part < CONCENTRATION_FLAG or (deltas[top] > 0) != (net > 0):
            continue
        name = top if dim == "location" else _names(db, tenant_id, [top]).get(top, "(unknown)")
        verb = "fell" if net < 0 else "rose"
        d = {"dimension": dim, "name": name, "change": round(deltas[top], 2), "share_of_change": round(part, 4),
             "previous": round(prev.get(top, 0.0), 2), "current": round(cur.get(top, 0.0), 2)}
        result["drivers"].append(d)
        result["statements"].append(
            f"{part*100:.0f}% of the change in {entity} came from {dim} '{name}' "
            f"({d['previous']:,.0f} -> {d['current']:,.0f}); total {verb} by {abs(net):,.0f}.")
    return result
