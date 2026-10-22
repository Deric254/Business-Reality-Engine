"""
Sections 12, 14, 34: data quality report, data discovery, and the one-screen
business health summary. All numbers are computed from the tenant's own data
at request time; nothing here is stored or estimated by a model.
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.core.coverage import compute_and_upsert_coverage
from app.core.detection import ACTIVE_STATES, _sum_metric
from app.models.alerts import Alert, AlertSeverity
from app.models.config import MonitoredMetric
from app.models.core import BusinessEvent, Connector

E = BusinessEvent


def _pct(n: float, d: float) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def quality_report(db: Session, tenant_id: str, days: int = 30, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    start = now - timedelta(days=days)
    flt = [E.tenant_id == tenant_id, E.timestamp >= start, E.timestamp < now + timedelta(days=1)]
    total, failed = db.query(func.count(E.id), func.count(case((E.validation_status == "failed", 1)))).filter(*flt).one()
    sales = [*flt, E.entity == "sale"]
    n_sales, no_cust, no_prod, no_amt, no_loc = db.query(
        func.count(E.id), func.count(case((E.customer_id.is_(None), 1))), func.count(case((E.product_id.is_(None), 1))),
        func.count(case((E.amount.is_(None), 1))), func.count(case((E.location.is_(None), 1)))).filter(*sales).one()
    # most common failure reasons
    reasons: dict[str, int] = {}
    for errs, in db.query(E.validation_errors).filter(*flt, E.validation_status == "failed").limit(5000):
        for e in errs or []:
            reasons[e] = reasons.get(e, 0) + 1
    by_conn = [{"connector": name, "events": int(n), "failed": int(f), "failed_pct": _pct(f, n)}
               for name, n, f in db.query(Connector.name, func.count(E.id), func.count(case((E.validation_status == "failed", 1))))
               .join(E, E.connector_id == Connector.id).filter(Connector.tenant_id == tenant_id, *flt)
               .group_by(Connector.name).order_by(func.count(case((E.validation_status == "failed", 1))).desc()).limit(20)]
    fail_pct = _pct(failed, total) or 0.0
    missing = {"customer": _pct(no_cust, n_sales), "product": _pct(no_prod, n_sales),
               "amount": _pct(no_amt, n_sales), "location": _pct(no_loc, n_sales)}
    score = None
    if total:
        # 100 minus failed share, minus a capped penalty for missing links/amounts on sales.
        penalty = min(30.0, 0.15 * (missing["amount"] or 0) + 0.1 * (missing["customer"] or 0) + 0.1 * (missing["product"] or 0))
        score = round(max(0.0, 100.0 - fail_pct - penalty), 1)
    notes = []
    if fail_pct >= 5:
        notes.append(f"{fail_pct:.0f}% of records could not be used (for example a missing date or amount) and are left out of every total.")
    if (missing["customer"] or 0) >= 30:
        notes.append(f"{missing['customer']:.0f}% of sales have no customer name, so we cannot say much about your customers.")
    if (missing["amount"] or 0) >= 5:
        notes.append(f"{missing['amount']:.0f}% of sales have no amount, so your sales total is probably too low.")
    if any(r.startswith("no FX rate") for r in reasons):
        notes.append("Some records are in another currency and are waiting for an exchange rate. Add it in Business settings.")
    return {"period_days": days, "events": int(total), "failed": int(failed), "failed_pct": fail_pct, "score": score,
            "sales_missing_pct": missing, "top_failure_reasons": sorted(
                ({"reason": r, "count": c} for r, c in reasons.items()), key=lambda x: -x["count"])[:10],
            "by_connector": by_conn, "notes": notes}


def discover(db: Session, tenant_id: str, now: Optional[datetime] = None) -> dict:
    """What the data contains, and which metrics are worth watching that are not watched yet."""
    now = now or datetime.utcnow()
    rows = db.query(E.entity, func.count(E.id), func.min(E.timestamp), func.max(E.timestamp),
                    func.count(E.amount), func.count(E.quantity), func.count(E.customer_id), func.count(E.product_id),
                    func.count(E.location)).filter(E.tenant_id == tenant_id, E.validation_status == "validated").group_by(E.entity).all()
    monitored = {(m.entity, m.field) for m in db.query(MonitoredMetric).filter(MonitoredMetric.tenant_id == tenant_id, MonitoredMetric.enabled == True)}  # noqa: E712
    entities, suggestions = [], []
    for ent, n, first, last, n_amt, n_qty, n_c, n_p, n_l in rows:
        span_days = max(1, (last - first).days + 1)
        entities.append({"entity": ent, "events": int(n), "first": first.isoformat(), "last": last.isoformat(), "span_days": span_days,
                         "has_amount_pct": _pct(n_amt, n), "has_quantity_pct": _pct(n_qty, n), "has_customer_pct": _pct(n_c, n),
                         "has_product_pct": _pct(n_p, n), "has_location_pct": _pct(n_l, n)})
        for field, have in (("amount", n_amt), ("quantity", n_qty)):
            if have < 20 or have / n < 0.5:
                continue
            per_week = have / (span_days / 7)
            period = 7 if per_week >= 20 else 30
            enough = span_days >= period * 5
            suggestions.append({
                "entity": ent, "field": field, "period_days": period, "direction": "rise" if ent == "expense" else "drop",
                "already_monitored": (ent, field) in monitored, "enough_history": enough,
                "reason": (f"{have} {ent} records with {field}, about {per_week:.0f} per week" +
                           ("" if enough else f"; only {span_days} days of history so far (need ~{period * 5})"))})
    suggestions.sort(key=lambda s: (s["already_monitored"], not s["enough_history"]))
    return {"entities": entities, "suggested_metrics": suggestions,
            "next_steps": [] if entities else ["No data yet. Connect a source or add a few entries by hand to begin."]}


def health_summary(db: Session, tenant_id: str, days: int = 30, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    cur_s, prev_s = now - timedelta(days=days), now - timedelta(days=2 * days)
    def tot(ent, s, e): return _sum_metric(db, tenant_id, ent, "amount", s, e)
    rev, exp = tot("sale", cur_s, now), tot("expense", cur_s, now)
    prev_rev, prev_exp = tot("sale", prev_s, cur_s), tot("expense", prev_s, cur_s)
    def chg(c, p): return round((c - p) / p * 100, 1) if p else None
    open_counts = {sev.value: n for sev, n in db.query(Alert.severity, func.count(Alert.id)).filter(
        Alert.tenant_id == tenant_id, Alert.state.in_(ACTIVE_STATES)).group_by(Alert.severity)}
    top = (db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.state.in_(ACTIVE_STATES),
                                  Alert.severity != AlertSeverity.opportunity)
           .order_by(Alert.priority_rank.desc().nullslast(), Alert.created_at.desc()).first())
    coverage = compute_and_upsert_coverage(db, tenant_id, now)
    stale = [c.area for c in coverage if c.status.value in ("stale", "not_connected")]
    q = quality_report(db, tenant_id, days, now)
    crit, high, med = open_counts.get("critical", 0), open_counts.get("high", 0), open_counts.get("medium", 0)
    score = 100 - crit * 25 - high * 10 - med * 3 - len(stale) * 8
    if q["score"] is not None:
        score -= (100 - q["score"]) * 0.2
    score = int(max(0, min(100, round(score))))
    has_data = bool(q["events"])
    status = "no_data" if not has_data else "attention" if crit else "watch" if (high or stale or score < 80) else "healthy"
    parts = []
    if not has_data:
        parts.append("No data has arrived in this period yet.")
    else:
        if crit or high:
            parts.append(f"{crit + high} serious problem{'s' if crit + high != 1 else ''} need{'' if crit + high != 1 else 's'} your attention.")
        if stale:
            parts.append(f"These data sources have stopped sending recent records: {', '.join(stale)}.")
        if not parts:
            parts.append("Nothing needs urgent attention.")
    return {"period_days": days, "status": status, "score": score, "headline": " ".join(parts),
            "financials": {"sales": round(rev, 2), "expenses": round(exp, 2), "profit": round(rev - exp, 2),
                           "sales_change_pct": chg(rev, prev_rev), "expenses_change_pct": chg(exp, prev_exp),
                           "profit_change_pct": chg(rev - exp, prev_rev - prev_exp)},
            "open_alerts": open_counts, "top_alert": ({"id": top.id, "title": top.title, "severity": top.severity.value} if top else None),
            "stale_sources": stale, "data_quality_score": q["score"],
            "score_basis": "100 minus 25 per critical, 10 per high, 3 per medium alert, 8 per stale source, and 20% of the data-quality gap"}
