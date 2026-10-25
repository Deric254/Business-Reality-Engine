"""
What got better and what got worse, since the period before: the same complete-day windows as everywhere else
(app/core/periods.py), so these lines can never disagree with the Overview or Analytics.

Two kinds of line, each ranked by how much money moved:
  the business as a whole  sales, spending, profit
  its parts                each branch (sales), each product (sales), each spending category (spending)

Direction matters: a rise in sales is an improvement, a rise in spending is a deterioration. Small wobbles are not news,
so a part must move by at least MIN_PCT of its own previous figure AND by at least MIN_SHARE_PCT of the business total.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core import analytics, periods
from app.core.detection import money_formatter
from app.models.core import BusinessEvent as E

MIN_PCT, MIN_SHARE_PCT, TOP = 10.0, 2.0, 5


def _line(area: str, name: str, now_: float, before: float, good_when_up: bool, fmt, min_amount: float) -> Optional[dict]:
    delta = now_ - before
    if abs(delta) < max(min_amount, 0.01):
        return None
    pct = None if not before else round(delta / abs(before) * 100, 1)
    if before and abs(pct) < MIN_PCT:
        return None
    better = (delta > 0) == good_when_up
    verb = "up" if delta > 0 else "down"
    detail = f"{fmt(before)} to {fmt(now_)}" + (f" ({verb} {abs(pct):.0f}%)" if pct is not None else " (new this period)")
    return {"area": area, "name": name, "kind": "improved" if better else "worse", "now": round(now_, 2), "before": round(before, 2),
            "change": round(delta, 2), "change_pct": pct, "detail": detail}


def build(db: Session, tenant_id: str, days: int, now: Optional[datetime] = None) -> dict:
    per = periods.resolve(db, tenant_id, days, now)
    base = {"period": per.as_dict(), "days": days, "improved": [], "worse": []}
    if per.prev_start is None or days == 1:
        return {**base, "note": "Pick a period of 7 days or more to see what changed against the one before." if days != 0 else
                "All time has nothing earlier to compare with. Pick a shorter period to see what changed."}
    fmt = money_formatter(db, tenant_id)
    lines: list[dict] = []

    def whole(label: str, entity: str, good_up: bool):
        c = analytics._totals(db, tenant_id, entity, per.start, per.end)["amount"]
        p = analytics._totals(db, tenant_id, entity, per.prev_start, per.prev_end)["amount"]
        return c, p, _line("Business", label, c, p, good_up, fmt, 0.0)

    sc, sp, l1 = whole("Sales", "sale", True)
    ec, ep, l2 = whole("Spending", "expense", False)
    pl = _line("Business", "Profit", sc - ec, sp - ep, True, fmt, 0.0)
    lines += [x for x in (l1, l2, pl) if x]
    if not (sc or sp or ec or ep):
        return {**base, "note": "No records in this period or the one before it."}

    def parts(area: str, entity: str, column, total: float, good_up: bool):
        cur = analytics._by(db, tenant_id, entity, column, per.start, per.end)
        was = analytics._by(db, tenant_id, entity, column, per.prev_start, per.prev_end)
        floor = total * MIN_SHARE_PCT / 100
        for n in set(cur) | set(was):
            if n == analytics.NOT_RECORDED:
                continue
            x = _line(area, n, cur.get(n, {}).get("amount", 0.0), was.get(n, {}).get("amount", 0.0), good_up, fmt, floor)
            if x:
                lines.append(x)

    parts("Branch", "sale", E.location, max(sc, sp), True)
    parts("Product", "sale", E.product_id, max(sc, sp), True)
    parts("Spending category", "expense", E.product_id, max(ec, ep), False)
    for kind in ("improved", "worse"):
        base[kind] = sorted([x for x in lines if x["kind"] == kind], key=lambda x: -abs(x["change"]))[:TOP]
    return base
