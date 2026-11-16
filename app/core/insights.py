"""What the business's own data says about where money is stuck or leaking: what it is, why, since when, what it costs, and what to do.

Every sentence is built from the records and carries its numbers; nothing here is guessed by an AI or taken from outside. A finding
exists only when the data on hand can support it (a product is "not selling" only if sales have been on record long enough to say so).
The three read the new kinds of data (stock on hand, what customers owe) together with the sales history:

  dead_stock       stock that has stopped selling: units and money tied up, when each product last sold, what it used to sell.
  stock_out_risk   products that are selling and will run out before they can be restocked, with the sales that would be lost.
  overdue_credit   money customers owe, how old it is, who it is, and whether the same customers are still being given more.

Each finding: {kind, title, severity, what, why, since, cost: {amount, basis}, evidence: [{label, value}], fix: [steps], rows}."""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.detection import data_start, money_formatter
from app.models.core import BusinessEvent as E

DEAD_AFTER_DAYS = 60            # a product with no sale for this long, on a business whose sales go back at least as far
COVER_DAYS = 7                  # a product with less stock than this many days of its recent sales is about to run out
OVERDUE_AFTER_DAYS = 30
SELLING_WINDOW_DAYS = 30
TOP = 5


def _valid(q, tenant_id):
    return q.filter(E.tenant_id == tenant_id, E.validation_status == "validated")


def _latest_stock(db: Session, tenant_id: str) -> list[dict]:
    """One row per product: the most recent stock figure (the same product from two tables counts once, the latest wins)."""
    rows = _valid(db.query(E.product_id, E.quantity, E.amount, E.metadata_json, E.timestamp), tenant_id).filter(E.entity == "stock", E.product_id.isnot(None)).order_by(E.timestamp).all()
    latest = {}
    for pid, qty, unit, meta, ts in rows:
        latest[pid] = {"product": pid, "name": (meta or {}).get("label") or pid, "qty": float(qty or 0), "unit": float(unit) if unit is not None else None, "as_of": ts}
    return list(latest.values())


def _sales_by_product(db: Session, tenant_id: str, since: datetime):
    """{product: (last_sale, first_sale, units_in_window, revenue_in_window, units_before_window)}"""
    base = _valid(db.query(E.product_id, func.max(E.timestamp), func.min(E.timestamp)), tenant_id).filter(E.entity == "sale", E.product_id.isnot(None)).group_by(E.product_id).all()
    win = {p: (float(u or 0), float(r or 0)) for p, u, r in _valid(db.query(E.product_id, func.sum(func.coalesce(E.quantity, 1)), func.sum(E.amount)), tenant_id)
           .filter(E.entity == "sale", E.product_id.isnot(None), E.timestamp >= since).group_by(E.product_id)}
    return {p: {"last": last, "first": first, "units": win.get(p, (0.0, 0.0))[0], "revenue": win.get(p, (0.0, 0.0))[1]} for p, last, first in base}


def _days(a: datetime, b: datetime) -> int:
    return max(0, (a - b).days)


def dead_stock(db: Session, tenant_id: str, now: datetime) -> Optional[dict]:
    first = data_start(db, tenant_id)
    stock = _latest_stock(db, tenant_id)
    if not stock or first is None or _days(now, first) < DEAD_AFTER_DAYS:
        return None                                                       # not enough sales history to say a product has stopped selling
    sold = _sales_by_product(db, tenant_id, now - timedelta(days=DEAD_AFTER_DAYS))
    fmt = money_formatter(db, tenant_id)
    dead = []
    for s in stock:
        if s["qty"] <= 0:
            continue
        sale = sold.get(s["product"])
        if sale and sale["units"] > 0:                                    # sold inside the window: alive
            continue
        silent = _days(now, sale["last"]) if sale else _days(now, first)
        dead.append({**s, "silent_days": silent, "never_sold": sale is None, "value": s["qty"] * s["unit"] if s["unit"] is not None else None})
    if not dead:
        return None
    dead.sort(key=lambda d: (-(d["value"] or 0), -d["silent_days"]))
    value = sum(d["value"] or 0 for d in dead)
    priced = any(d["value"] is not None for d in dead)
    units = sum(d["qty"] for d in dead)
    share = value / sum((s["qty"] * s["unit"]) for s in stock if s["unit"] is not None) if priced else None
    never = sum(1 for d in dead if d["never_sold"])
    oldest = max(dead, key=lambda d: d["silent_days"])
    rows = [{"product": d["name"], "units": d["qty"], "value": d["value"], "not_sold_for_days": d["silent_days"], "never_sold": d["never_sold"]} for d in dead[:TOP]]
    what = f"{len(dead)} products ({units:,.0f} units) have not sold in {DEAD_AFTER_DAYS} days or more" + (f", with {fmt(value)} of stock tied up in them" + (f" ({share:.0%} of all stock value)" if share else "") if priced else "") + "."
    why = ("The sales records show these products were " + (f"bought at some point but have stopped: the longest silent one, {oldest['name']}, last sold {oldest['silent_days']} days ago. " if len(dead) > never else "")
           + (f"{never} of them have no sale on record at all since the records began. " if never else "")
           + "Stock that does not move holds cash, takes shelf space, and loses value as it ages or expires.")
    return {"kind": "dead_stock", "title": "Stock that has stopped selling", "severity": "high" if share and share >= 0.2 else "medium", "what": what, "why": why,
            "since": (now - timedelta(days=oldest["silent_days"])).date().isoformat(), "cost": {"amount": round(value, 2), "basis": "cost of the units still on hand"} if priced else None,
            "evidence": [{"label": "Products not selling", "value": str(len(dead))}, {"label": "Units on hand", "value": f"{units:,.0f}"}, {"label": "Never sold", "value": str(never)}],
            "fix": ["Stop reordering these products until the stock is gone.", "Put the largest ones on offer or bundle them with fast sellers, starting with those silent the longest.",
                    "Ask the supplier whether unsold stock can be returned or swapped.", "Check whether anything changed when each one stopped selling: price, supplier, a competitor, a stock-out."], "rows": rows}


def stock_out_risk(db: Session, tenant_id: str, now: datetime) -> Optional[dict]:
    stock = {s["product"]: s for s in _latest_stock(db, tenant_id)}
    if not stock:
        return None
    window = SELLING_WINDOW_DAYS
    sold = _sales_by_product(db, tenant_id, now - timedelta(days=window))
    fmt = money_formatter(db, tenant_id)
    first = data_start(db, tenant_id)
    span = min(window, _days(now, first)) if first else 0
    if span < 7:
        return None
    risky = []
    for p, s in stock.items():
        sale = sold.get(p)
        if not sale or sale["units"] <= 0:
            continue
        per_day = sale["units"] / span
        cover = s["qty"] / per_day
        if cover < COVER_DAYS:
            risky.append({"product": s["name"], "units": s["qty"], "per_day": per_day, "cover": cover, "daily_revenue": sale["revenue"] / span})
    if not risky:
        return None
    risky.sort(key=lambda r: -r["daily_revenue"])
    at_risk = sum(r["daily_revenue"] * (COVER_DAYS - r["cover"]) for r in risky)
    rows = [{"product": r["product"], "units_left": r["units"], "days_of_stock": round(r["cover"], 1), "sells_per_day": round(r["per_day"], 1)} for r in risky[:TOP]]
    return {"kind": "stock_out_risk", "title": "Fast sellers about to run out", "severity": "high" if any(r["cover"] < 2 for r in risky) else "medium",
            "what": f"{len(risky)} products that sold in the last {window} days have less than {COVER_DAYS} days of stock left.",
            "why": f"At their recent selling rate, the first, {risky[0]['product']}, runs out in {risky[0]['cover']:.1f} days. A customer who finds it missing buys elsewhere, and some do not come back.",
            "since": now.date().isoformat(), "cost": {"amount": round(at_risk, 2), "basis": f"sales that would be missed if each waits {COVER_DAYS} days for stock"},
            "evidence": [{"label": "Products at risk", "value": str(len(risky))}, {"label": "Lowest cover", "value": f"{min(r['cover'] for r in risky):.1f} days"}, {"label": "Sales per day at risk", "value": fmt(sum(r['daily_revenue'] for r in risky))}],
            "fix": ["Reorder these first, largest daily sales first.", "Set a reorder level for each at your supplier's delivery time plus a few days of sales.", "Tell the person ordering which ones sell fastest."], "rows": rows}


def overdue_credit(db: Session, tenant_id: str, now: datetime) -> Optional[dict]:
    rows = _valid(db.query(E.entity_id, E.customer_id, E.amount, E.timestamp, E.metadata_json), tenant_id).filter(E.entity == "receivable", E.amount > 0).all()
    if not rows:
        return None
    fmt = money_formatter(db, tenant_id)
    owed = sum(float(r.amount) for r in rows)
    overdue = [r for r in rows if _days(now, r.timestamp) >= OVERDUE_AFTER_DAYS]
    if not overdue:
        return None
    by_customer: dict = {}
    for r in overdue:
        c = by_customer.setdefault(r.customer_id or "Unnamed", {"owed": 0.0, "oldest": 0})
        c["owed"] += float(r.amount)
        c["oldest"] = max(c["oldest"], _days(now, r.timestamp))
    late = sum(float(r.amount) for r in overdue)
    top = sorted(by_customer.items(), key=lambda kv: -kv[1]["owed"])
    recent = _valid(db.query(E.customer_id, func.sum(E.amount)), tenant_id).filter(E.entity == "sale", E.timestamp >= now - timedelta(days=30), E.customer_id.in_(list(by_customer))).group_by(E.customer_id).all()
    still_buying = {c: float(a) for c, a in recent if a}
    month_sales = float(_valid(db.query(func.coalesce(func.sum(E.amount), 0)), tenant_id).filter(E.entity == "sale", E.timestamp >= now - timedelta(days=30)).scalar() or 0)
    days_of_sales = f" That is about {late / month_sales * 30:.0f} days of the business's sales." if month_sales > 0 else ""
    oldest = max(c["oldest"] for c in by_customer.values())
    why = (f"Of the {fmt(owed)} customers owe, {fmt(late)} is more than {OVERDUE_AFTER_DAYS} days old; the oldest is {oldest} days.{days_of_sales} "
           + (f"{len(still_buying)} of the customers who are overdue bought again in the last 30 days ({fmt(sum(still_buying.values()))}): they are being given more credit while old balances stand." if still_buying else "")
           ).strip()
    out_rows = [{"customer": c, "owed": v["owed"], "oldest_days": v["oldest"], "bought_last_30_days": still_buying.get(c, 0.0)} for c, v in top[:TOP]]
    return {"kind": "overdue_credit", "title": "Money customers owe and have not paid", "severity": "high" if month_sales and late > month_sales * 0.25 else "medium",
            "what": f"{fmt(late)} owed by {len(by_customer)} customers has been due for more than {OVERDUE_AFTER_DAYS} days.", "why": why,
            "since": (now - timedelta(days=oldest)).date().isoformat(), "cost": {"amount": round(late, 2), "basis": "overdue and not yet collected"},
            "evidence": [{"label": "Total owed", "value": fmt(owed)}, {"label": "Overdue", "value": fmt(late)}, {"label": "Customers overdue", "value": str(len(by_customer))}],
            "fix": ["Contact the largest overdue customers first; the top few usually hold most of the money.", "Stop giving more credit to a customer whose old balance is past due.",
                    "Set a credit limit and a payment date for each credit customer, and write them on the invoice.", "Offer a small discount for paying early to those who pay late by habit."], "rows": out_rows}


FINDERS = (dead_stock, stock_out_risk, overdue_credit)


def findings(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[dict]:
    """Every finding the data supports, biggest money first. An empty list means nothing was found, not that nothing was checked."""
    now = now or utcnow()
    out = [f for f in (finder(db, tenant_id, now) for finder in FINDERS) if f]
    return sorted(out, key=lambda f: -((f["cost"] or {}).get("amount") or 0))
