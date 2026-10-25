"""
Business analytics for the Analytics page: everything an owner or a lender asks of a set of books, computed from the
validated records and nothing else.

  headline figures   sales, spending, profit, margin, average sale, customers, each against the period before
  monthly series     sales, spending, profit and margin per month (the shape of the year)
  branches           sales, spending, profit and margin of each branch or location
  products           what sells, how concentrated it is (Pareto), what is growing or shrinking
  spending           where the money goes, by category, against the period before
  weekdays           which days of the week carry the business
  customers          who buys, how concentrated, new against returning
  daily              a day by day record for the calendar heat map
  forecast           a straight-line projection of weekly sales, with its fit quality and an honest band
  insights           plain sentences generated from the figures above, never from a model's guess

Every total uses the same period boundaries as the rest of the product (app/core/periods.py: whole days on the
business's own calendar), so a figure here can never disagree with the Overview. Only validated records count.
"""
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import periods
from app.core.detection import money_formatter
from app.core.forecasting import _least_squares
from app.models.core import BusinessEvent as E

# A generic buyer is not one customer: "Walk-in Customer" is everybody who paid cash, so it says nothing about dependence.
_GENERIC_BUYER = re.compile(r"walk[\s-]*in|^cash|unknown|anonymous|general|^customer$|^n/?a$|^none$|misc|various", re.I)
NOT_RECORDED = "Not recorded"
HEATMAP_DAYS = 182            # 26 weeks
FORECAST_WEEKS, FORECAST_AHEAD, MIN_FORECAST_WEEKS = 12, 4, 6
_Z80 = 1.2816                 # an 80% band, as the existing daily forecast uses


def is_generic_buyer(name: Optional[str]) -> bool:
    return bool(name) and bool(_GENERIC_BUYER.search(name.strip()))


def _pct(cur: float, prev: float) -> Optional[float]:
    return None if not prev else round((cur - prev) / abs(prev) * 100, 1)


def _metric(cur: float, prev: Optional[float], digits: int = 2) -> dict:
    return {"current": round(cur, digits), "previous": None if prev is None else round(prev, digits),
            "change_pct": None if prev is None else _pct(cur, prev)}


def _base(tenant_id: str, entity: str, start: datetime, end: datetime):
    return (E.tenant_id == tenant_id, E.entity == entity, E.validation_status == "validated", E.amount.isnot(None),
            E.timestamp >= start, E.timestamp < end)


def _totals(db: Session, tenant_id: str, entity: str, start: datetime, end: datetime) -> dict:
    n, total, customers = db.query(func.count(E.id), func.coalesce(func.sum(E.amount), 0),
                                   func.count(func.distinct(E.customer_id))).filter(*_base(tenant_id, entity, start, end)).one()
    return {"count": int(n), "amount": float(total), "customers": int(customers)}


def _by(db: Session, tenant_id: str, entity: str, column, start: datetime, end: datetime) -> dict:
    """{value: {amount, count, quantity}} for one dimension. A record with no value is grouped as 'Not recorded'."""
    rows = (db.query(column, func.coalesce(func.sum(E.amount), 0), func.count(E.id), func.coalesce(func.sum(E.quantity), 0))
            .filter(*_base(tenant_id, entity, start, end)).group_by(column).all())
    return {(v if v not in (None, "") else NOT_RECORDED): {"amount": float(a), "count": int(n), "quantity": float(q)} for v, a, n, q in rows}


def _share(part: float, whole: float) -> Optional[float]:
    return round(part / whole * 100, 1) if whole else None


# ---------------- sections ----------------

def _headline(db, tenant_id, per) -> dict:
    cur_s, cur_e = _totals(db, tenant_id, "sale", per.start, per.end), _totals(db, tenant_id, "expense", per.start, per.end)
    prev_s = prev_e = None
    if per.prev_start is not None:
        prev_s, prev_e = _totals(db, tenant_id, "sale", per.prev_start, per.prev_end), _totals(db, tenant_id, "expense", per.prev_start, per.prev_end)

    def margin(s, e):
        return None if not s or not s["amount"] else round((s["amount"] - e["amount"]) / s["amount"] * 100, 1)

    def avg(t):
        return 0.0 if not t or not t["count"] else t["amount"] / t["count"]

    profit_c = cur_s["amount"] - cur_e["amount"]
    profit_p = None if prev_s is None else prev_s["amount"] - prev_e["amount"]
    m_c, m_p = margin(cur_s, cur_e), margin(prev_s, prev_e)
    return {
        "sales": _metric(cur_s["amount"], None if prev_s is None else prev_s["amount"]),
        "spending": _metric(cur_e["amount"], None if prev_e is None else prev_e["amount"]),
        "profit": _metric(profit_c, profit_p),
        "margin_pct": {"current": m_c, "previous": m_p, "change_points": None if m_c is None or m_p is None else round(m_c - m_p, 1)},
        "average_sale": _metric(avg(cur_s), None if prev_s is None else avg(prev_s)),
        "transactions": _metric(cur_s["count"], None if prev_s is None else prev_s["count"], 0),
        "customers": _metric(cur_s["customers"], None if prev_s is None else prev_s["customers"], 0),
        "spending_ratio_pct": {"current": _share(cur_e["amount"], cur_s["amount"]), "previous": None if prev_s is None else _share(prev_e["amount"], prev_s["amount"])},
    }


def _monthly(db, tenant_id, per) -> list[dict]:
    """The last 12 calendar months (fewer if the records are younger), the current month marked partial."""
    local = func.timezone(per.tz.key, func.timezone("UTC", E.timestamp))
    first_of_this = per.last_day.replace(day=1)
    y, m = first_of_this.year, first_of_this.month - 11
    while m < 1:
        m += 12; y -= 1
    lo_day = date(y, m, 1)
    first_record = periods.first_record_day(db, tenant_id, per.tz)
    if first_record and first_record > lo_day:
        lo_day = first_record.replace(day=1)
    lo = periods.local_midnight_utc(lo_day, per.tz)
    cell: dict = {}
    for d, ent, amt in (db.query(func.date_trunc("month", local), E.entity, func.coalesce(func.sum(E.amount), 0))
                        .filter(E.tenant_id == tenant_id, E.entity.in_(("sale", "expense")), E.validation_status == "validated",
                                E.timestamp >= lo, E.timestamp < per.end)
                        .group_by(func.date_trunc("month", local), E.entity).all()):
        cell[(d.date(), ent)] = float(amt)
    out, d = [], lo_day
    while d <= first_of_this:
        s, e = cell.get((d, "sale"), 0.0), cell.get((d, "expense"), 0.0)
        out.append({"month": d.isoformat(), "sales": round(s, 2), "spending": round(e, 2), "profit": round(s - e, 2),
                    "margin_pct": _share(s - e, s), "partial": d == first_of_this})
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def _dim_table(cur: dict, prev: dict) -> list[dict]:
    rows = []
    for name, c in cur.items():
        p = prev.get(name)
        rows.append({"name": name, "amount": round(c["amount"], 2), "count": c["count"], "quantity": round(c["quantity"], 2),
                     "previous": None if not prev else round(p["amount"], 2) if p else 0.0,
                     "change_pct": _pct(c["amount"], p["amount"]) if p else None})
    return sorted(rows, key=lambda r: -r["amount"])


def _branches(db, tenant_id, per) -> list[dict]:
    sales = _by(db, tenant_id, "sale", E.location, per.start, per.end)
    spend = _by(db, tenant_id, "expense", E.location, per.start, per.end)
    prev_sales = _by(db, tenant_id, "sale", E.location, per.prev_start, per.prev_end) if per.prev_start is not None else {}
    total_s = sum(v["amount"] for v in sales.values())
    rows = []
    for name in set(sales) | set(spend):
        s, e = sales.get(name, {}).get("amount", 0.0), spend.get(name, {}).get("amount", 0.0)
        p = prev_sales.get(name)
        rows.append({"name": name, "sales": round(s, 2), "spending": round(e, 2), "profit": round(s - e, 2), "margin_pct": _share(s - e, s),
                     "share_pct": _share(s, total_s), "transactions": sales.get(name, {}).get("count", 0),
                     "average_sale": round(s / sales[name]["count"], 2) if sales.get(name, {}).get("count") else 0.0,
                     "sales_change_pct": _pct(s, p["amount"]) if p else None})
    return sorted(rows, key=lambda r: -r["sales"])[:12]


def _products(db, tenant_id, per) -> dict:
    cur = _by(db, tenant_id, "sale", E.product_id, per.start, per.end)
    prev = _by(db, tenant_id, "sale", E.product_id, per.prev_start, per.prev_end) if per.prev_start is not None else {}
    table = _dim_table(cur, prev)
    total = sum(r["amount"] for r in table)
    running, to_80 = 0.0, None
    for i, r in enumerate(table):
        running += r["amount"]
        r["share_pct"], r["cumulative_pct"] = _share(r["amount"], total), _share(running, total)
        if to_80 is None and total and running / total >= 0.8:
            to_80 = i + 1
    top = table[:15]
    rest = table[15:]
    return {"top": top, "count": len(table), "others": {"count": len(rest), "amount": round(sum(r["amount"] for r in rest), 2),
                                                        "share_pct": _share(sum(r["amount"] for r in rest), total)},
            "products_for_80pct": to_80}


def _spending(db, tenant_id, per) -> dict:
    cur = _by(db, tenant_id, "expense", E.product_id, per.start, per.end)       # the category an expense was for
    prev = _by(db, tenant_id, "expense", E.product_id, per.prev_start, per.prev_end) if per.prev_start is not None else {}
    table = _dim_table(cur, prev)
    total = sum(r["amount"] for r in table)
    for r in table:
        r["share_pct"] = _share(r["amount"], total)
    top, rest = table[:10], table[10:]
    if rest:
        top.append({"name": f"{len(rest)} other categories", "amount": round(sum(r["amount"] for r in rest), 2), "count": sum(r["count"] for r in rest),
                    "quantity": 0.0, "previous": None, "change_pct": None, "share_pct": _share(sum(r["amount"] for r in rest), total)})
    return {"categories": top, "total": round(total, 2)}


def _daily_rows(db, tenant_id, per, lo: datetime) -> dict:
    local = func.timezone(per.tz.key, func.timezone("UTC", E.timestamp))
    out: dict = defaultdict(lambda: {"sales": 0.0, "spending": 0.0})
    for d, ent, amt in (db.query(func.date(local), E.entity, func.coalesce(func.sum(E.amount), 0))
                        .filter(E.tenant_id == tenant_id, E.entity.in_(("sale", "expense")), E.validation_status == "validated",
                                E.amount.isnot(None), E.timestamp >= lo, E.timestamp < per.end)
                        .group_by(func.date(local), E.entity).all()):
        out[d]["sales" if ent == "sale" else "spending"] += float(amt)
    return out


def _weekdays(daily: dict, per) -> list[dict]:
    """Average sales on each weekday of the period, counting every calendar day of it (a closed Sunday counts as zero)."""
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    tot, n = [0.0] * 7, [0] * 7
    d = per.first_day
    while d <= per.last_day:
        if d < per.last_day or d in daily:             # today is only counted once it has records: it is still in progress
            tot[d.weekday()] += daily[d]["sales"] if d in daily else 0.0
            n[d.weekday()] += 1
        d += timedelta(days=1)
    return [{"day": names[i], "average_sales": round(tot[i] / n[i], 2) if n[i] else 0.0, "days": n[i]} for i in range(7)]


def _customers(db, tenant_id, per) -> dict:
    cur = _by(db, tenant_id, "sale", E.customer_id, per.start, per.end)
    cur.pop(NOT_RECORDED, None)
    total_sales = _totals(db, tenant_id, "sale", per.start, per.end)["amount"]
    rows = sorted(cur.items(), key=lambda kv: -kv[1]["amount"])
    named = [(k, v) for k, v in rows if not is_generic_buyer(k)]
    top = [{"name": k, "amount": round(v["amount"], 2), "orders": v["count"], "share_pct": _share(v["amount"], total_sales),
            "generic": is_generic_buyer(k)} for k, v in rows[:10]]
    first_seen = dict(db.query(E.customer_id, func.min(E.timestamp))
                      .filter(E.tenant_id == tenant_id, E.entity == "sale", E.validation_status == "validated", E.customer_id.isnot(None),
                              E.customer_id.in_([k for k, _ in named] or [""])).group_by(E.customer_id).all())
    new = sum(1 for k, _ in named if first_seen.get(k) and first_seen[k] >= per.start and not per.all_time)
    repeat = sum(1 for _, v in named if v["count"] >= 2)
    return {"top": top, "named_count": len(named), "new_customers": new if not per.all_time else None,
            "returning_customers": (len(named) - new) if not per.all_time else None,
            "repeat_rate_pct": _share(repeat, len(named)),
            "top_customer_share_pct": _share(named[0][1]["amount"], total_sales) if named else None,
            "top5_share_pct": _share(sum(v["amount"] for _, v in named[:5]), total_sales) if named else None,
            "generic_share_pct": _share(sum(v["amount"] for k, v in rows if is_generic_buyer(k)), total_sales)}


def _forecast(daily: dict, per) -> dict:
    """Straight-line trend of complete weeks (the 7 days ending yesterday, then the 7 before that...). It says how well the
    line fits; a noisy business gets a wide band and a plain warning, never a confident number it has not earned."""
    today = per.last_day
    weeks = []
    for k in range(FORECAST_WEEKS - 1, -1, -1):
        start = today - timedelta(days=7 * (k + 1))
        weeks.append({"week_start": start.isoformat(), "sales": round(sum(daily[start + timedelta(days=i)]["sales"] for i in range(7) if (start + timedelta(days=i)) in daily), 2),
                      "spending": round(sum(daily[start + timedelta(days=i)]["spending"] for i in range(7) if (start + timedelta(days=i)) in daily), 2)})
    while weeks and weeks[0]["sales"] <= 0:                 # before the records began is not a week of zero sales
        weeks.pop(0)
    out = {"weeks": weeks, "projection": [], "method": "straight-line trend of your complete weeks", "r_squared": None, "reliable": False}
    n = len(weeks)
    if n < MIN_FORECAST_WEEKS:
        out["note"] = f"Needs at least {MIN_FORECAST_WEEKS} complete weeks of sales; there are {n}."
        return out
    xs, ys = list(range(n)), [w["sales"] for w in weeks]
    slope, intercept = _least_squares(xs, ys)
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    sd = math.sqrt(sum(r * r for r in resid) / max(1, n - 2))
    my = sum(ys) / n
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum(r * r for r in resid) / ss_tot if ss_tot else 0.0
    mx = (n - 1) / 2
    sxx = sum((x - mx) ** 2 for x in xs) or 1.0
    last = date.fromisoformat(weeks[-1]["week_start"])
    for i in range(1, FORECAST_AHEAD + 1):
        x0 = n - 1 + i
        mid = max(0.0, intercept + slope * x0)
        half = _Z80 * sd * math.sqrt(1 + 1 / n + (x0 - mx) ** 2 / sxx)
        out["projection"].append({"week_start": (last + timedelta(days=7 * i)).isoformat(), "expected": round(mid, 2),
                                  "low": round(max(0.0, mid - half), 2), "high": round(mid + half, 2)})
    out.update(r_squared=round(r2, 3), slope_per_week=round(slope, 2), reliable=r2 >= 0.3,
               average_week=round(my, 2), weekly_noise_pct=round(sd / my * 100, 1) if my else None)
    if r2 < 0.3:
        out["note"] = "Weekly sales swing more than any trend explains, so treat the band as the honest answer and the middle line as a guide only."
    return out


def _insights(h: dict, branches: list, products: dict, spending: dict, weekdays: list, customers: dict, forecast: dict, fmt, days: int) -> list[dict]:
    """Plain sentences, each built from a figure above. An insight appears only when the data clearly supports it."""
    out: list[dict] = []
    sales, margin = h["sales"], h["margin_pct"]
    cmp_ = f"the {days} days before" if days else "earlier"
    if margin["current"] is not None:
        if margin["current"] < 0:
            out.append({"tone": "bad", "title": "You spent more than you sold",
                        "text": f"Spending was {fmt(h['spending']['current'])} against sales of {fmt(sales['current'])}, a loss of {fmt(-h['profit']['current'])} ({margin['current']:.0f}% margin)."})
        elif margin["change_points"] is not None and abs(margin["change_points"]) >= 3:
            up = margin["change_points"] > 0
            out.append({"tone": "good" if up else "warn", "title": f"Profit margin {'improved' if up else 'slipped'} {abs(margin['change_points']):.1f} points",
                        "text": f"You kept {margin['current']:.0f} of every 100 sold after recorded spending, against {margin['previous']:.0f} in {cmp_}."})
        else:
            out.append({"tone": "info", "title": f"You keep about {margin['current']:.0f}% of what you sell",
                        "text": f"Profit after recorded spending was {fmt(h['profit']['current'])} on sales of {fmt(sales['current'])}."})
    real = [b for b in branches if b["name"] != NOT_RECORDED]
    if len(real) >= 2:
        best, worst = max(real, key=lambda b: b["profit"]), min(real, key=lambda b: b["profit"])
        out.append({"tone": "info", "title": f"{best['name']} earns the most",
                    "text": f"{best['name']} made {fmt(best['profit'])} profit on {fmt(best['sales'])} of sales"
                            + ((f"; {worst['name']} " + (f"lost {fmt(-worst['profit'])}." if worst['profit'] < 0 else f"made {fmt(worst['profit'])}.")) if worst is not best else ".")})
        loss = [b for b in real if b["profit"] < 0 and b["spending"] > 0]
        if loss:
            out.append({"tone": "bad", "title": f"{loss[0]['name']} is losing money",
                        "text": f"Its recorded spending of {fmt(loss[0]['spending'])} is above its sales of {fmt(loss[0]['sales'])}."})
        fall = [b for b in real if b["sales_change_pct"] is not None and b["sales_change_pct"] <= -15 and (b["share_pct"] or 0) >= 10]
        if fall:
            b = min(fall, key=lambda r: r["sales_change_pct"])
            out.append({"tone": "warn", "title": f"{b['name']} sales are down {abs(b['sales_change_pct']):.0f}%",
                        "text": f"It carries {b['share_pct']:.0f}% of your sales, so this matters more than a small branch's dip."})
    top = products["top"]
    if len(top) >= 3 and products["count"] >= 5:
        k = products["products_for_80pct"]
        out.append({"tone": "info", "title": f"{top[0]['name']} leads, and {k} of {products['count']} products make 80% of sales",
                    "text": f"The top three bring {top[2]['cumulative_pct']:.0f}% of sales. " + ("A short list carries the business: keep those in stock first." if k and k * 2 <= products['count'] else "Sales are spread across most of the range.")})
    cats = [c for c in spending["categories"] if c.get("share_pct") is not None and "other categories" not in c["name"]]
    if cats:
        c0 = cats[0]
        out.append({"tone": "info", "title": f"{c0['name']} is {c0['share_pct']:.0f}% of your spending",
                    "text": f"{fmt(c0['amount'])} of {fmt(spending['total'])} spent" + (f", {('up' if c0['change_pct'] > 0 else 'down')} {abs(c0['change_pct']):.0f}% on {cmp_}." if c0["change_pct"] is not None else ".")})
        grown = [c for c in cats if c["change_pct"] is not None and c["change_pct"] >= 25 and (c["share_pct"] or 0) >= 5]
        if grown:
            g = max(grown, key=lambda c: c["amount"] - (c["previous"] or 0))
            out.append({"tone": "warn", "title": f"{g['name']} spending is up {g['change_pct']:.0f}%",
                        "text": f"From {fmt(g['previous'] or 0)} to {fmt(g['amount'])}. It is {g['share_pct']:.0f}% of everything you spent."})
    days_ = [w for w in weekdays if w["days"] >= 3]
    if len(days_) >= 5:
        hi, lo = max(days_, key=lambda w: w["average_sales"]), min(days_, key=lambda w: w["average_sales"])
        if lo["average_sales"] > 0 and hi["average_sales"] / lo["average_sales"] >= 1.3:
            out.append({"tone": "info", "title": f"{hi['day']}s are your best day, {lo['day']}s your weakest",
                        "text": f"{hi['day']}s average {fmt(hi['average_sales'])} against {fmt(lo['average_sales'])} on {lo['day']}s ({hi['average_sales'] / lo['average_sales']:.1f} times as much). Plan staff and stock around it."})
    tc = customers.get("top_customer_share_pct")
    if tc is not None and tc >= 20 and customers["top"]:
        name = next((c["name"] for c in customers["top"] if not c["generic"]), None)
        if name:
            out.append({"tone": "warn" if tc >= 30 else "info", "title": f"{name} is {tc:.0f}% of your sales",
                        "text": "Losing one customer that size would hurt. Look for a second customer of similar weight."})
    if customers.get("new_customers") and customers["named_count"] >= 5:
        out.append({"tone": "info", "title": f"{customers['returning_customers']} returning and {customers['new_customers']} new named customers",
                    "text": f"{customers['repeat_rate_pct']:.0f}% of your named customers bought more than once in this period."})
    if forecast["projection"] and forecast["reliable"]:
        nxt = sum(p["expected"] for p in forecast["projection"])
        recent = sum(w["sales"] for w in forecast["weeks"][-FORECAST_AHEAD:])
        ch = _pct(nxt, recent)
        if ch is not None:
            out.append({"tone": "good" if ch > 3 else "warn" if ch < -3 else "info", "title": f"The trend points to about {fmt(nxt)} over the next {FORECAST_AHEAD} weeks",
                        "text": f"That is {abs(ch):.0f}% {'above' if ch >= 0 else 'below'} the last {FORECAST_AHEAD} complete weeks. It is a straight-line estimate, not a promise."})
    unrecorded = next((b for b in branches if b["name"] == NOT_RECORDED), None)
    if unrecorded and (unrecorded["share_pct"] or 0) >= 10:
        out.append({"tone": "warn", "title": f"{unrecorded['share_pct']:.0f}% of sales have no branch",
                    "text": "Branch figures understate reality until the branch is recorded on every sale."})
    return out


# ---------------- entry point ----------------

def build_analytics(db: Session, tenant_id: str, days: int, now: Optional[datetime] = None) -> dict:
    per = periods.resolve(db, tenant_id, days, now)
    fmt = money_formatter(db, tenant_id)
    heat_lo = min(per.start, periods.local_midnight_utc(per.last_day - timedelta(days=HEATMAP_DAYS - 1), per.tz),
                  periods.local_midnight_utc(per.last_day - timedelta(days=7 * (FORECAST_WEEKS + 1)), per.tz))
    daily = _daily_rows(db, tenant_id, per, heat_lo)
    headline = _headline(db, tenant_id, per)
    branches = _branches(db, tenant_id, per)
    products = _products(db, tenant_id, per)
    spending = _spending(db, tenant_id, per)
    weekdays = _weekdays(daily, per) if (per.last_day - per.first_day).days >= 13 or per.all_time else []
    customers = _customers(db, tenant_id, per)
    forecast = _forecast(daily, per)
    heat_from = per.last_day - timedelta(days=HEATMAP_DAYS - 1)
    return {
        "period": per.as_dict(), "days": days,
        "headline": headline,
        "monthly": _monthly(db, tenant_id, per),
        "branches": branches, "products": products, "spending": spending,
        "weekdays": weekdays, "customers": customers,
        "daily": [{"date": d.isoformat(), "sales": round(v["sales"], 2), "spending": round(v["spending"], 2)}
                  for d, v in sorted(daily.items()) if d >= heat_from],
        "forecast": forecast,
        # Today is not over, so comparing it with whole days would only say "down" every morning: no verdicts for it.
        "insights": [] if days == 1 else _insights(headline, branches, products, spending, weekdays, customers, forecast, fmt, days),
    }
