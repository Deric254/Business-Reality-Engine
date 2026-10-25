"""
Alerts about WHERE and WHY, not only HOW MUCH (Sections 19-20, 27).

The overall detectors (detection.py, detectors.py) judge the two totals: sales and spending. A business can be in
trouble while both totals look fine: one branch sliding while another climbs, one cost line exploding inside a flat
total, a margin eroding, one customer carrying the business, one payment that should not exist. These detectors look at
the records behind the totals, name the branch, category, customer or payment, and say how much of the problem it is.

  category spike       a spending category far above its own usual (any category, not just the biggest)
  branch decline       a branch whose sales fall well below its own usual, even when the total hides it
  margin compression   a branch (or the whole business) keeping much less of each sale than it used to, or losing money
  customer dependence  one named customer carrying a large share of sales
  large payment        one payment far outside the size this business normally pays

Same lifecycle rules as every detector: an open alert for the same dedup_key is updated, never duplicated; a person
closing it keeps it quiet (cooldown); a cleared condition closes the alert. Everything compares whole days up to `now`
(the engine passes the start of today), 30-day windows against the three 30-day windows before (90 for customers).
"""
import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import coverage as coverage_mod
from app.core.analytics import is_generic_buyer
from app.core.detection import ACTIVE_STATES, _size_of_change, _sum_metric, close_if_cleared, money_formatter, resolve_alert
from app.core.detectors import _upsert
from app.core.tenant_time import tenant_zone
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import BusinessEvent as E

logger = logging.getLogger("business_reality_engine")

WINDOW, BASELINES = 30, 3
MIN_BASELINE_WINDOWS = 2           # a "usual" needs at least two earlier windows with records

SPIKE_MIN_RISE, SPIKE_MIN_EXTRA_SHARE = 0.50, 0.05        # +50% on its usual, and the extra is 5%+ of all spending
DECLINE_MIN_DROP, DECLINE_MIN_SHARE = 0.20, 0.10          # -20% on its usual, for a branch that is 10%+ of sales
MARGIN_MIN_DROP_POINTS = 15.0                              # keeps 15+ points less of each sale than usual
CONCENTRATION_DAYS, CONCENTRATION_MEDIUM, CONCENTRATION_HIGH, CONCENTRATION_MIN_ORDERS = 90, 0.25, 0.40, 3
LARGE_DAYS, LARGE_HISTORY_DAYS, LARGE_MIN_HISTORY = 7, 90, 20
LARGE_TIMES_MEDIAN, LARGE_TIMES_P95, LARGE_MIN_SHARE_OF_SALES = 8.0, 3.0, 0.05
LARGE_STALE_DAYS = 14


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "none"


def _windows(now: datetime, days: int = WINDOW, n: int = BASELINES):
    cur = (now - timedelta(days=days), now)
    base = [(now - timedelta(days=days * (i + 1)), now - timedelta(days=days * i)) for i in range(1, n + 1)]
    return cur, base


def _group(db: Session, tenant_id: str, entity: str, column, start: datetime, end: datetime, *where) -> dict[str, float]:
    rows = (db.query(column, func.coalesce(func.sum(E.amount), 0))
            .filter(E.tenant_id == tenant_id, E.entity == entity, E.validation_status == "validated", E.amount.isnot(None),
                    E.timestamp >= start, E.timestamp < end, column.isnot(None), *where)
            .group_by(column).all())
    return {k: float(v) for k, v in rows}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _coverage(db, tenant_id, entity, now):
    return coverage_mod.coverage_pct_for_entity(db, tenant_id, entity, now=now)


def _headline_size(change: float) -> str:
    """'4.7 times' reads better than '370%'; below double it is 'up 62%'."""
    return _size_of_change(change)


def _payment_alert_covers(db: Session, tenant_id: str, category: str) -> bool:
    """A single large payment already explains a category's spike, in its own alert. Two alerts about one event only confuse."""
    return any((a.where or {}).get("category") == category
               for a in db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.kind == "payment", Alert.state.in_(ACTIVE_STATES)).all())


# ---------------- 1. a spending category far above its own usual ----------------

def detect_category_spikes(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[Alert]:
    now = now or datetime.utcnow()
    cur_w, base_w = _windows(now)
    cur = _group(db, tenant_id, "expense", E.product_id, *cur_w)
    bases = [_group(db, tenant_id, "expense", E.product_id, *w) for w in base_w]
    total_spent = sum(cur.values())
    sold = _sum_metric(db, tenant_id, "sale", "amount", *cur_w)
    fmt = money_formatter(db, tenant_id)
    out: list[Alert] = []
    for cat in sorted(set(cur).union(*bases)):
        positives = [b[cat] for b in bases if b.get(cat, 0) > 0]
        key = f"category_spike:expense:{_slug(cat)}:{WINDOW}d"
        if len(positives) < MIN_BASELINE_WINDOWS:
            continue                                           # an irregular cost has no usual to compare with
        usual, amt = _mean(positives), cur.get(cat, 0.0)
        rise, extra = (amt - usual) / usual, amt - usual
        if rise < SPIKE_MIN_RISE or extra < SPIKE_MIN_EXTRA_SHARE * max(total_spent, 1.0):
            if rise < SPIKE_MIN_RISE / 2:
                close_if_cleared(db, tenant_id, key, now)
            continue
        where = _group(db, tenant_id, "expense", E.location, *cur_w, E.product_id == cat)
        top_loc = max(where, key=where.get) if where else None
        top = (db.query(E.amount, E.timestamp, E.entity_id).filter(
            E.tenant_id == tenant_id, E.entity == "expense", E.validation_status == "validated", E.product_id == cat,
            E.timestamp >= cur_w[0], E.timestamp < cur_w[1], E.amount.isnot(None)).order_by(E.amount.desc()).first())
        zone = tenant_zone(db, tenant_id)
        one_off = bool(top and amt and float(top[0]) / amt >= 0.5 and float(top[0]) > 2 * usual / 3)
        if one_off and _payment_alert_covers(db, tenant_id, cat):
            continue
        text = (f"{cat} came to {fmt(amt)} in the last {WINDOW} complete days. In a usual {WINDOW} days it is about {fmt(usual)} "
                f"(the average of the {len(positives)} periods before). That is {fmt(extra)} more than usual, "
                f"{extra / total_spent * 100:.0f}% of everything you spent.")
        if top_loc and len(where) > 1 and where[top_loc] / sum(where.values()) >= 0.6:
            text += f" {top_loc} accounts for {where[top_loc] / sum(where.values()) * 100:.0f}% of it."
        if one_off:
            day = top[1].replace(tzinfo=timezone.utc).astimezone(zone).strftime("%d %b")
            text += f" One payment of {fmt(float(top[0]))} on {day}{' (ref ' + top[2] + ')' if top[2] else ''} is {float(top[0]) / amt * 100:.0f}% of it."
            action = (f"Start with the payment of {fmt(float(top[0]))} on {day}. If it was planned (stock, equipment, a loan repayment), "
                      f"tap 'Not relevant' and this stays quiet. If you do not recognise it, find out who approved it.")
        else:
            action = f"Look at what {cat.lower()} paid for in the last {WINDOW} complete days: which branch, which supplier, and whether the price or the quantity grew."
        severity = (AlertSeverity.critical if sold and extra >= 0.25 * sold else AlertSeverity.high if rise >= 1.0 else AlertSeverity.medium)
        joiner = "" if rise >= 1 else " from"
        a = _upsert(
            db, tenant_id, key, now,
            {"metric": "expense.amount", "dimension": "category", "category": cat, "current": round(amt, 2), "usual": round(usual, 2),
             "change_pct": round(rise * 100, 1), "window_days": WINDOW, "baseline_windows": len(positives), "top_location": top_loc, "one_off": one_off},
            dict(kind="category", severity=severity, title=f"{cat} spending is {_headline_size(rise)}{joiner} your usual {WINDOW} days",
                 what_happened=text, magnitude=round(rise, 4), period_start=cur_w[0], period_end=cur_w[1],
                 where={"category": cat, "location": top_loc}, confidence=0.9 if len(positives) >= BASELINES else 0.75,
                 data_coverage_pct=_coverage(db, tenant_id, "expense", now), recommended_action=action),
        )
        if a:
            out.append(a)
    return out


# ---------------- 2. a branch whose sales fall below its own usual ----------------

def detect_branch_declines(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[Alert]:
    now = now or datetime.utcnow()
    cur_w, base_w = _windows(now)
    cur = _group(db, tenant_id, "sale", E.location, *cur_w)
    bases = [_group(db, tenant_id, "sale", E.location, *w) for w in base_w]
    places = set(cur).union(*bases)
    if len(places) < 2:
        return []                                            # one place: the overall comparison already says it
    totals = [sum(b.values()) for b in bases if sum(b.values()) > 0]
    if len(totals) < MIN_BASELINE_WINDOWS:
        return []
    usual_total = _mean(totals)
    fmt = money_formatter(db, tenant_id)
    out: list[Alert] = []
    for place in sorted(places):
        positives = [b[place] for b in bases if b.get(place, 0) > 0]
        key = f"branch_decline:sale:{_slug(place)}:{WINDOW}d"
        if len(positives) < MIN_BASELINE_WINDOWS:
            continue
        usual, amt = _mean(positives), cur.get(place, 0.0)
        share = usual / usual_total
        drop = (usual - amt) / usual
        if share < DECLINE_MIN_SHARE or drop < DECLINE_MIN_DROP:
            if drop < DECLINE_MIN_DROP / 2:
                close_if_cleared(db, tenant_id, key, now)
            continue
        others_now = sum(v for k, v in cur.items() if k != place)
        others_usual = _mean([sum(v for k, v in b.items() if k != place) for b in bases if sum(v for k, v in b.items() if k != place) > 0] or [0.0])
        others_change = (others_now - others_usual) / others_usual if others_usual else None
        prod_now = _group(db, tenant_id, "sale", E.product_id, *cur_w, E.location == place)
        prod_before = [_group(db, tenant_id, "sale", E.product_id, *w, E.location == place) for w in base_w]
        prod_usual = {p: _mean([b.get(p, 0.0) for b in prod_before]) for p in set().union(*prod_before)}
        falls = sorted(((prod_usual[p] - prod_now.get(p, 0.0), p) for p in prod_usual), reverse=True)
        lost = usual - amt
        text = (f"{place} sold {fmt(amt)} in the last {WINDOW} complete days. In a usual {WINDOW} days it sells about {fmt(usual)} "
                f"(the average of the {len(positives)} periods before): {fmt(lost)} less, and it carries {share * 100:.0f}% of your sales.")
        if others_change is not None:
            text += (f" Your other branches together are {'up' if others_change >= 0 else 'down'} {abs(others_change) * 100:.0f}%"
                     f"{', which hides this in your total.' if others_change > 0 else '.'}")
        if falls and falls[0][0] > 0 and falls[0][0] / lost >= 0.25:
            text += f" Most of the fall is {falls[0][1]}: down {fmt(falls[0][0])} ({falls[0][0] / lost * 100:.0f}% of it)."
        severity = (AlertSeverity.critical if drop >= 0.40 and share >= 0.25 else AlertSeverity.high if drop >= 0.30 else AlertSeverity.medium)
        a = _upsert(
            db, tenant_id, key, now,
            {"metric": "sale.amount", "dimension": "branch", "branch": place, "current": round(amt, 2), "usual": round(usual, 2),
             "change_pct": round(-drop * 100, 1), "share_of_sales_pct": round(share * 100, 1), "window_days": WINDOW,
             "others_change_pct": None if others_change is None else round(others_change * 100, 1), "baseline_windows": len(positives)},
            dict(kind="branch", severity=severity, title=f"{place} sales are down {drop * 100:.0f}% from its usual {WINDOW} days",
                 what_happened=text, magnitude=round(-drop, 4), period_start=cur_w[0], period_end=cur_w[1], where={"location": place},
                 confidence=0.8 if len(positives) >= BASELINES else 0.65, data_coverage_pct=_coverage(db, tenant_id, "sale", now),
                 recommended_action=(f"Find out what changed at {place}: stock, staff, opening hours, a competitor, prices"
                                     + (f", and especially {falls[0][1]}." if falls and falls[0][0] > 0 and falls[0][0] / lost >= 0.25 else "."))),
        )
        if a:
            out.append(a)
    return out


# ---------------- 3. a branch (or the business) keeping less of each sale ----------------

def _margin(sold: float, spent: float) -> Optional[float]:
    return None if sold <= 0 else (sold - spent) / sold


def detect_margin_compression(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[Alert]:
    now = now or datetime.utcnow()
    cur_w, base_w = _windows(now)
    sales_c, spend_c = (_group(db, tenant_id, e, E.location, *cur_w) for e in ("sale", "expense"))
    sales_b = [_group(db, tenant_id, "sale", E.location, *w) for w in base_w]
    spend_b = [_group(db, tenant_id, "expense", E.location, *w) for w in base_w]
    fmt = money_formatter(db, tenant_id)
    units: dict[str, tuple[float, float, list[tuple[float, float]]]] = {}
    for place in set(sales_c).union(*sales_b):                            # a branch only counts when it records its own spending
        pairs = [(s.get(place, 0.0), x.get(place, 0.0)) for s, x in zip(sales_b, spend_b)]
        if sum(1 for s, x in pairs if s > 0 and x > 0) >= MIN_BASELINE_WINDOWS:
            units[place] = (sales_c.get(place, 0.0), spend_c.get(place, 0.0), pairs)
    all_pairs = [(_sum_metric(db, tenant_id, "sale", "amount", *w), _sum_metric(db, tenant_id, "expense", "amount", *w)) for w in base_w]
    units["__all__"] = (_sum_metric(db, tenant_id, "sale", "amount", *cur_w), _sum_metric(db, tenant_id, "expense", "amount", *cur_w), all_pairs)
    out: list[Alert] = []
    for unit, (s_c, x_c, pairs) in units.items():
        name = "Your business" if unit == "__all__" else unit
        key = f"margin:{'all' if unit == '__all__' else _slug(unit)}:{WINDOW}d"
        m_c = _margin(s_c, x_c)
        usual_ms = [m for m in (_margin(s, x) for s, x in pairs) if m is not None]
        if m_c is None or len(usual_ms) < MIN_BASELINE_WINDOWS:
            continue
        m_u = _mean(usual_ms)
        drop_pts = (m_u - m_c) * 100
        loss = m_c < 0 and unit != "__all__"                              # the business-wide loss has its own alert (Spending exceeds sales)
        if unit == "__all__" and m_c < 0:
            continue
        if not loss and drop_pts < MARGIN_MIN_DROP_POINTS:
            if drop_pts < MARGIN_MIN_DROP_POINTS / 2:
                close_if_cleared(db, tenant_id, key, now)
            continue
        where_ = () if unit == "__all__" else (E.location == unit,)
        exp_now = _group(db, tenant_id, "expense", E.product_id, *cur_w, *where_)
        exp_before = [_group(db, tenant_id, "expense", E.product_id, *w, *where_) for w in base_w]
        exp_usual = {c: _mean([b.get(c, 0.0) for b in exp_before]) for c in set().union(*exp_before)}
        grew = sorted(((exp_now.get(c, 0.0) - exp_usual.get(c, 0.0), c) for c in set(exp_now) | set(exp_usual)), reverse=True)
        driver = grew[0] if grew and grew[0][0] > 0 else None
        if loss:
            title = f"{name} spent more than it sold"
            text = (f"In the last {WINDOW} complete days {name} sold {fmt(s_c)} and recorded {fmt(x_c)} of spending, a loss of {fmt(x_c - s_c)}. "
                    f"In a usual {WINDOW} days it keeps {m_u * 100:.0f}% of each sale.")
            severity = AlertSeverity.high
        else:
            title = f"{name} keeps {drop_pts:.0f} points less of each sale than usual"
            text = (f"In the last {WINDOW} complete days {name} kept {m_c * 100:.0f}% of each sale after recorded spending; in a usual {WINDOW} days it keeps "
                    f"{m_u * 100:.0f}%. On sales of {fmt(s_c)} that is about {fmt(drop_pts / 100 * s_c)} less profit than the usual margin would give.")
            severity = AlertSeverity.high if drop_pts >= 25 else AlertSeverity.medium
        if driver:
            text += f" The biggest cause is {driver[1]}: {fmt(driver[0])} more than usual."
        a = _upsert(
            db, tenant_id, key, now,
            {"metric": "profit.margin", "dimension": "business" if unit == "__all__" else "branch", "branch": None if unit == "__all__" else unit,
             "margin_pct": round(m_c * 100, 1), "usual_margin_pct": round(m_u * 100, 1), "drop_points": round(drop_pts, 1), "sales": round(s_c, 2),
             "spending": round(x_c, 2), "window_days": WINDOW, "loss": loss, "driver": driver[1] if driver else None},
            dict(kind="margin", severity=severity, title=title, what_happened=text, magnitude=round(-drop_pts / 100, 4),
                 period_start=cur_w[0], period_end=cur_w[1], where=None if unit == "__all__" else {"location": unit}, confidence=0.8,
                 data_coverage_pct=_coverage(db, tenant_id, "expense", now),
                 recommended_action=(f"Start with {driver[1]}: it grew {fmt(driver[0])} on its usual. " if driver else "")
                 + "Check whether prices kept up with costs, and that every sale is being recorded."),
        )
        if a:
            out.append(a)
    return out


# ---------------- 4. one named customer carrying the business ----------------

def detect_customer_concentration(db: Session, tenant_id: str, now: Optional[datetime] = None) -> Optional[Alert]:
    now = now or datetime.utcnow()
    start = now - timedelta(days=CONCENTRATION_DAYS)
    key = f"concentration:customer:{CONCENTRATION_DAYS}d"
    total = _sum_metric(db, tenant_id, "sale", "amount", start, now)
    by = _group(db, tenant_id, "sale", E.customer_id, start, now)
    named = {k: v for k, v in by.items() if not is_generic_buyer(k)}
    if total <= 0 or not named:
        return None
    top = max(named, key=named.get)
    share = named[top] / total
    orders = db.query(func.count(E.id)).filter(E.tenant_id == tenant_id, E.entity == "sale", E.validation_status == "validated",
                                               E.customer_id == top, E.timestamp >= start, E.timestamp < now).scalar()
    if share < CONCENTRATION_MEDIUM or orders < CONCENTRATION_MIN_ORDERS:
        if share < CONCENTRATION_MEDIUM * 0.8:
            close_if_cleared(db, tenant_id, key, now)
        return None
    fmt = money_formatter(db, tenant_id)
    top3 = sorted(named.values(), reverse=True)[:3]
    return _upsert(
        db, tenant_id, key, now,
        {"metric": "sale.amount", "dimension": "customer", "customer": top, "share_pct": round(share * 100, 1), "amount": round(named[top], 2),
         "orders": int(orders), "top3_share_pct": round(sum(top3) / total * 100, 1), "window_days": CONCENTRATION_DAYS},
        dict(kind="concentration", severity=AlertSeverity.high if share >= CONCENTRATION_HIGH else AlertSeverity.medium,
             title=f"{top} is {share * 100:.0f}% of your sales",
             what_happened=(f"In the last {CONCENTRATION_DAYS} complete days {top} bought {fmt(named[top])} in {orders} orders, {share * 100:.0f}% of all your sales. "
                            f"Your three biggest named customers together are {sum(top3) / total * 100:.0f}%. If {top} slows down or leaves, "
                            f"about {fmt(named[top] / CONCENTRATION_DAYS * 30)} a month goes with them."),
             magnitude=round(share, 4), period_start=start, period_end=now, where={"customer": top}, confidence=0.9,
             data_coverage_pct=_coverage(db, tenant_id, "sale", now),
             recommended_action=f"Agree terms or a regular order with {top} while things are good, and work on a second customer of similar size."),
    )


# ---------------- 5. one payment far outside what this business normally pays ----------------

def detect_large_payments(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[Alert]:
    now = now or datetime.utcnow()
    w_start, h_start = now - timedelta(days=LARGE_DAYS), now - timedelta(days=LARGE_HISTORY_DAYS)
    hist = [float(a) for (a,) in db.query(E.amount).filter(
        E.tenant_id == tenant_id, E.entity == "expense", E.validation_status == "validated", E.amount.isnot(None), E.amount > 0,
        E.timestamp >= h_start, E.timestamp < w_start).all()]
    out: list[Alert] = []
    if len(hist) >= LARGE_MIN_HISTORY:
        med = statistics.median(hist)
        p95 = sorted(hist)[min(len(hist) - 1, int(len(hist) * 0.95))]
        sales_30 = _sum_metric(db, tenant_id, "sale", "amount", now - timedelta(days=30), now)
        spent_30 = _sum_metric(db, tenant_id, "expense", "amount", now - timedelta(days=30), now)
        floor = max(LARGE_TIMES_MEDIAN * med, LARGE_TIMES_P95 * p95, LARGE_MIN_SHARE_OF_SALES * sales_30)
        fmt, zone = money_formatter(db, tenant_id), tenant_zone(db, tenant_id)
        rows = (db.query(E.id, E.amount, E.timestamp, E.entity_id, E.product_id, E.location).filter(
            E.tenant_id == tenant_id, E.entity == "expense", E.validation_status == "validated", E.amount.isnot(None),
            E.timestamp >= w_start, E.timestamp < now, E.amount >= floor).order_by(E.amount.desc()).limit(10).all())
        for eid, amount, ts, ref, cat, loc in rows:
            amount = float(amount)
            day = ts.replace(tzinfo=timezone.utc).astimezone(zone).strftime("%d %b")
            times = amount / med if med else 0
            text = (f"A payment of {fmt(amount)} was recorded on {day}{' for ' + cat if cat else ''}{' at ' + loc if loc else ''}"
                    f"{' (ref ' + ref + ')' if ref else ''}. Your usual payment is about {fmt(med)}, so this is {times:.0f} times the usual, "
                    f"and bigger than 19 in 20 of your payments in the last {LARGE_HISTORY_DAYS} days. "
                    f"It is {amount / spent_30 * 100:.0f}% of everything you spent in the last 30 days"
                    + (f", and {amount / sales_30 * 100:.0f}% of what you sold." if sales_30 else "."))
            severity = AlertSeverity.critical if sales_30 and amount >= 0.5 * sales_30 else AlertSeverity.high
            a = _upsert(
                db, tenant_id, f"large_payment:{eid}", now,
                {"metric": "expense.amount", "dimension": "payment", "event_id": eid, "amount": round(amount, 2), "date": ts.date().isoformat(),
                 "reference": ref, "category": cat, "location": loc, "usual_payment": round(med, 2), "times_usual": round(times, 1)},
                dict(kind="payment", severity=severity, title=f"A payment of {fmt(amount)} is {times:.0f} times your usual payment",
                     what_happened=text, magnitude=round(times, 2), period_start=ts, period_end=ts + timedelta(days=1),
                     where={"category": cat, "location": loc}, confidence=0.9, data_coverage_pct=_coverage(db, tenant_id, "expense", now),
                     recommended_action=(f"Check {'ref ' + ref if ref else 'this payment'} first. If it was planned (stock, equipment, a loan repayment), "
                                         f"tap 'Not relevant' and this alert stays quiet. If you do not recognise it, find out who approved it.")),
            )
            if a:
                out.append(a)
    # A payment alert nobody has touched is closed once the payment is old news; one a person is working on stays open.
    for open_ in (db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.kind == "payment", Alert.state == AlertState.detected).all()):
        if open_.period_start and now - open_.period_start > timedelta(days=LARGE_STALE_DAYS):
            resolve_alert(db, open_, now)
    return out


# ---------------- the set ----------------

def run_dimension_detectors(db: Session, tenant_id: str, now: Optional[datetime] = None) -> tuple[list[Alert], int]:
    """All five. One failing detector never stops the others. Returns (alerts touched, how many detectors failed)."""
    alerts: list[Alert] = []
    errors = 0
    for name, fn in (("large payments", detect_large_payments), ("category spikes", detect_category_spikes),   # payments first: they explain a spike
                     ("branch declines", detect_branch_declines), ("margin compression", detect_margin_compression),
                     ("customer concentration", detect_customer_concentration)):
        try:
            found = fn(db, tenant_id, now)
            alerts += found if isinstance(found, list) else [found] if found else []
        except Exception as e:
            db.rollback()
            errors += 1
            logger.error(f"Detector '{name}' failed for tenant={tenant_id}: {e}")
    return alerts, errors
