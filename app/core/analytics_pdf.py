"""
The Analytics report as a PDF: the same figures, insights and alerts the Analytics page shows, laid out as one
continuous document that flows from page to page. It is built here, not by the browser's print dialog, so the result
is identical in every browser, never leaves a half-empty page, and does not depend on any AI service: every sentence
in it is worked out from the records (app/core/analytics.py).

build_analytics_pdf() is a pure function of the analytics dict, so it is cheap to test and has no database access.
"""
import io
import math
from datetime import date
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Polygon, PolyLine, Rect, String
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, CondPageBreak, Flowable, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle

# The palette of the Analytics page: one fixed hue per measure.
SALES, SPENDING, PROFIT = HexColor("#0c9a88"), HexColor("#d1790a"), HexColor("#2c6fe0")
INK, MUTED, FAINT = HexColor("#16211e"), HexColor("#46524d"), HexColor("#67726c")
GRID, WASH, BAR_OFF = HexColor("#e2e6df"), HexColor("#f6f7f4"), HexColor("#c9d2cc")
GOOD, BAD, WARN = HexColor("#146c3e"), HexColor("#b42318"), HexColor("#9a5b00")
HEAT = [HexColor(c) for c in ("#e4f4f1", "#a9ded6", "#5cc2b3", "#16998a", "#0a5f54")]
TONE = {"good": GOOD, "warn": WARN, "bad": BAD, "info": PROFIT}
SEVERITY = {"critical": BAD, "high": BAD, "medium": WARN, "informational": PROFIT, "opportunity": GOOD}

PAGE_W, PAGE_H = A4
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 16 * mm, 16 * mm, 16 * mm
WIDTH = PAGE_W - 2 * MARGIN_X
HALF = (WIDTH - 8 * mm) / 2
FONT, BOLD = "Helvetica", "Helvetica-Bold"


def _s(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, fontName=kw.pop("fontName", FONT), fontSize=kw.pop("fontSize", 9), leading=kw.pop("leading", 12),
                          textColor=kw.pop("textColor", INK), **kw)


ST = {
    "title": _s("title", fontName=BOLD, fontSize=26, leading=30),
    "meta": _s("meta", fontSize=10.5, leading=14, textColor=MUTED),
    "note": _s("note", fontSize=8.5, leading=11, textColor=FAINT),
    "h2": _s("h2", fontName=BOLD, fontSize=14, leading=17, spaceBefore=16, spaceAfter=2),
    "h2sub": _s("h2sub", fontSize=8.5, leading=11, textColor=MUTED, spaceAfter=6),
    "h3": _s("h3", fontName=BOLD, fontSize=10.5, leading=13, keepWithNext=1),
    "h3sub": _s("h3sub", fontSize=8, leading=10.5, textColor=MUTED, spaceAfter=3, keepWithNext=1),
    "label": _s("label", fontSize=8, leading=10, textColor=MUTED),
    "value": _s("value", fontName=BOLD, fontSize=17, leading=21),
    "small": _s("small", fontSize=8, leading=10.5),
    "cell": _s("cell", fontSize=8.5, leading=10.5),
    "cellr": _s("cellr", fontSize=8.5, leading=10.5, alignment=TA_RIGHT),
    "head": _s("head", fontName=BOLD, fontSize=7.5, leading=9.5, textColor=MUTED),
    "headr": _s("headr", fontName=BOLD, fontSize=7.5, leading=9.5, textColor=MUTED, alignment=TA_RIGHT),
    "body": _s("body", fontSize=8.8, leading=12),
}


def _t(value) -> str:
    """Text the built-in PDF fonts can draw: anything outside Windows Latin-1 becomes '?' instead of an empty box."""
    return str(value).encode("cp1252", "replace").decode("cp1252")


def _p(text, style: str, color=None) -> Paragraph:
    st = ST[style] if color is None else ParagraphStyle(style + "c", parent=ST[style], textColor=color)
    return Paragraph(escape(_t(text)), st)


def _rich(markup: str, style: str) -> Paragraph:
    """Markup the caller built from already-escaped parts (see _e)."""
    return Paragraph(markup, ST[style])


def _e(text, color=None, bold=False) -> str:
    out = escape(_t(text))
    if bold:
        out = f"<b>{out}</b>"
    return f'<font color="{color.hexval().replace("0x", "#")}">{out}</font>' if color is not None else out


def _n(v: float) -> str:
    return f"{v:,.0f}"


def _short(v: float) -> str:
    a = abs(v)
    if a >= 1e6:
        s, unit = v / 1e6, "M"
    elif a >= 1e3:
        s, unit = v / 1e3, "K"
    else:
        return f"{v:.0f}"
    return f"{s:.1f}".rstrip("0").rstrip(".") + unit


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


def _period_label(days: int) -> str:
    return "All time" if days == 0 else "12 months" if days == 365 else f"{days} days"


def _change(pct, good_when: str) -> tuple[str, object]:
    """('up 12%', colour) as the page shows it: words as well as colour, so it reads in black and white too."""
    if pct is None:
        return "new", FAINT
    if abs(pct) < 1:
        return "flat", MUTED
    good = (pct > 0) == (good_when == "up")
    size = f"{abs(pct) / 100:.0f}x" if abs(pct) >= 1000 else f"{abs(pct):.0f}%"
    return f"{'up' if pct > 0 else 'down'} {size}", GOOD if good else BAD


# ------------------------------------------------------------------------------------------------ charts

def _nice(lo: float, hi: float, count: int = 4) -> tuple[float, float, list[float]]:
    """A clean axis with round steps, always containing zero."""
    lo, hi = min(0.0, lo), max(0.0, hi, 1.0)
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s for s in (1, 2, 2.5, 5, 10) if raw / mag <= s) * mag
    start, end = math.floor(lo / step) * step, math.ceil(hi / step) * step
    return start, end, [start + i * step for i in range(round((end - start) / step) + 1)]


class _Plot:
    """A drawing with a y axis, grid lines, a legend and x positions for n categories."""

    def __init__(self, w: float, h: float, vmin: float, vmax: float, n: int, legend: tuple = ()):
        self.d, self.w, self.n = Drawing(w, h), w, n
        self.left, self.right, self.bottom, self.top = 32, 4, 14, 16 if legend else 4
        self.lo, self.hi, ticks = _nice(vmin, vmax)
        self.span_h = h - self.bottom - self.top
        for t in ticks:
            y = self.y(t)
            self.d.add(Line(self.left, y, w - self.right, y, strokeColor=INK if t == 0 else GRID, strokeWidth=0.6 if t == 0 else 0.4))
            self.d.add(String(self.left - 4, y - 2.5, _short(t), fontName=FONT, fontSize=6.5, fillColor=FAINT, textAnchor="end"))
        x = self.left
        for color, label in legend:
            self.d.add(Rect(x, h - 9, 6, 6, fillColor=color, strokeColor=None))
            self.d.add(String(x + 9, h - 8, label, fontName=FONT, fontSize=7, fillColor=MUTED))
            x += 15 + len(label) * 3.6

    @property
    def step(self) -> float:
        return (self.w - self.left - self.right) / self.n

    def x(self, i: float) -> float:
        return self.left + (i + 0.5) * self.step

    def y(self, v: float) -> float:
        return self.bottom + (v - self.lo) / (self.hi - self.lo) * self.span_h

    def label(self, i: float, text: str) -> None:
        self.d.add(String(self.x(i), 4, text, fontName=FONT, fontSize=6.5, fillColor=FAINT, textAnchor="middle"))

    def bar(self, i: float, value: float, color, offset: float = 0, width: float | None = None) -> None:
        width = width if width is not None else self.step * 0.62
        x0, y0, y1 = self.x(i) - width / 2 + offset, self.y(0), self.y(value)
        self.d.add(Rect(x0, min(y0, y1), width, max(abs(y1 - y0), 0.4), fillColor=color, strokeColor=None))

    def line(self, points: list[tuple[float, float]], color, dash: tuple | None = None, dots: bool = True) -> None:
        pts = [(self.x(i), self.y(v)) for i, v in points]
        if len(pts) > 1:
            self.d.add(PolyLine([c for p in pts for c in p], strokeColor=color, strokeWidth=1.4, strokeDashArray=dash))
        if dots:
            for x, y in pts:
                self.d.add(Rect(x - 1.5, y - 1.5, 3, 3, fillColor=color, strokeColor=white, strokeWidth=0.4))

    def band(self, lows: list[tuple[float, float]], highs: list[tuple[float, float]], color) -> None:
        pts = [(self.x(i), self.y(v)) for i, v in highs] + [(self.x(i), self.y(v)) for i, v in reversed(lows)]
        self.d.add(Polygon([c for p in pts for c in p], fillColor=color, fillOpacity=0.18, strokeColor=None))


def _month_label(iso: str) -> str:
    d = date.fromisoformat(iso[:7] + "-01")
    return f"{d:%b}" if d.month != 1 else f"{d:%b %y}"


def _monthly_chart(rows: list[dict], w: float, h: float) -> Drawing:
    values = [r[k] for r in rows for k in ("sales", "spending", "profit")]
    p = _Plot(w, h, min(values), max(values), len(rows), ((SALES, "Sales"), (SPENDING, "Spending"), (PROFIT, "Profit")))
    bw = p.step * 0.3
    for i, r in enumerate(rows):
        p.bar(i, r["sales"], SALES, -bw / 2, bw)
        p.bar(i, r["spending"], SPENDING, bw / 2, bw)
        p.label(i, _month_label(r["month"]))
    p.line([(i, r["profit"]) for i, r in enumerate(rows)], PROFIT)
    return p.d


def _margin_chart(rows: list[dict], w: float, h: float) -> Drawing:
    known = [(i, r["margin_pct"]) for i, r in enumerate(rows) if r["margin_pct"] is not None]
    p = _Plot(w, h, min([v for _, v in known] or [0]), max([v for _, v in known] or [0]), len(rows))
    for i, r in enumerate(rows):
        p.label(i, _month_label(r["month"]) if len(rows) <= 7 or i % 2 == 0 else "")
    p.line(known, SALES)
    return p.d


def _forecast_chart(fc: dict, w: float, h: float) -> Drawing:
    weeks, proj = fc["weeks"], fc["projection"]
    n = len(weeks) + len(proj)
    values = [x["sales"] for x in weeks] + [x[k] for x in proj for k in ("low", "high")]
    p = _Plot(w, h, min(values), max(values), n, ((SALES, "Weekly sales"), (PROFIT, "Outlook, 80% range")))
    p.line([(i, x["sales"]) for i, x in enumerate(weeks)], SALES)
    if proj:
        base = len(weeks)
        p.band([(base + i, x["low"]) for i, x in enumerate(proj)], [(base + i, x["high"]) for i, x in enumerate(proj)], PROFIT)
        p.line([(base - 1, weeks[-1]["sales"])] + [(base + i, x["expected"]) for i, x in enumerate(proj)], PROFIT, dash=(3, 2), dots=False)
    every = max(1, math.ceil(n / 6))
    for i, x in enumerate(weeks + proj):
        if i % every == 0:
            p.label(i, f"{date.fromisoformat(x['week_start']):%d %b}".lstrip("0"))
    return p.d


def _weekday_chart(rows: list[dict], w: float, h: float) -> Drawing:
    p = _Plot(w, h, 0, max(r["average_sales"] for r in rows), len(rows))
    top = max(rows, key=lambda r: r["average_sales"])
    for i, r in enumerate(rows):
        p.bar(i, r["average_sales"], SALES if r is top else BAR_OFF)
        p.label(i, r["day"][:3])
    return p.d


def _heatmap(daily: list[dict], last_day: str, w: float, h: float) -> Drawing:
    """Weeks across, weekdays down, darker where more was sold: quiet stretches and gaps in the records show at once."""
    weeks, gap, left, top, legend = 26, 2, 24, 12, 40
    cell_w, cell_h = (w - left - legend) / weeks - gap, (h - top) / 7 - gap
    last = date.fromisoformat(last_day)
    first_monday = last.fromordinal(last.toordinal() - last.weekday() - 7 * (weeks - 1))
    sales = {r["date"]: r["sales"] for r in daily}
    positive = sorted(v for v in sales.values() if v > 0)
    cuts = [positive[int(len(positive) * q)] for q in (0.2, 0.4, 0.6, 0.8)] if positive else []
    d = Drawing(w, h)
    for wk in range(weeks):
        monday = first_monday.fromordinal(first_monday.toordinal() + 7 * wk)
        x = left + wk * (cell_w + gap)
        if wk == 0 or monday.day <= 7:
            d.add(String(x, h - 8, f"{monday:%b}", fontName=FONT, fontSize=6.5, fillColor=FAINT))
        for dow in range(7):
            day = monday.fromordinal(monday.toordinal() + dow)
            if day > last:
                continue
            v = sales.get(day.isoformat(), 0)
            level = sum(v > c for c in cuts) + 1 if v > 0 else 0
            d.add(Rect(x, h - top - (dow + 1) * (cell_h + gap) + gap, cell_w, cell_h, fillColor=HEAT[level - 1] if level else WASH, strokeColor=None))
    for dow, name in ((0, "Mon"), (2, "Wed"), (4, "Fri"), (6, "Sun")):
        d.add(String(0, h - top - (dow + 1) * (cell_h + gap) + gap + cell_h / 2 - 2.3, name, fontName=FONT, fontSize=6.5, fillColor=FAINT))
    lx = w - legend + 10
    d.add(String(lx, h - top - 6, "Less", fontName=FONT, fontSize=6.5, fillColor=FAINT))
    for i, color in enumerate(HEAT):
        d.add(Rect(lx, h - top - 18 - i * 9, 8, 8, fillColor=color, strokeColor=None))
    d.add(String(lx, h - top - 18 - 5 * 9 - 3, "More", fontName=FONT, fontSize=6.5, fillColor=FAINT))
    return d


class _Fit(Flowable):
    """A chart that takes the height the page has left, within [low, high], so a page fills up instead of leaving a hole
    below a chart that was a little too tall. If even `low` does not fit, it starts the next page."""

    def __init__(self, make, width: float, low: float, high: float):
        super().__init__()
        self.make, self.width, self.low, self.high, self.height = make, width, low, high, high

    def wrap(self, _avail_w, avail_h):
        self.height = max(self.low, min(self.high, avail_h))
        return self.width, self.height

    def draw(self):
        self.make(self.width, self.height).drawOn(self.canv, 0, 0)


def _bar_cell(value: float, top: float, color, w: float) -> Drawing:
    d = Drawing(w, 9)
    d.add(Rect(0, 1.5, w, 6, fillColor=WASH, strokeColor=None))
    d.add(Rect(0, 1.5, max(w * value / top, 0.6) if top > 0 else 0.6, 6, fillColor=color, strokeColor=None))
    return d


# ------------------------------------------------------------------------------------------------ tables and blocks

_BASE_STYLE = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
               ("LINEBELOW", (0, 0), (-1, 0), 0.6, GRID), ("LINEBELOW", (0, 1), (-1, -1), 0.4, GRID)]


def _table(head: list[str], rows: list[list], widths: list[float], numeric: tuple = ()) -> Table:
    """A table that may break between rows (never inside one) and repeats its heading on each page."""
    cells = [[_p(h, "headr" if i in numeric else "head") for i, h in enumerate(head)]]
    cells += [[c if not isinstance(c, (str, int, float)) else _p(c, "cellr" if i in numeric else "cell") for i, c in enumerate(r)] for r in rows]
    t = Table(cells, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(_BASE_STYLE))
    return t


def _section(title: str, sub: str | None = None, need: float = 45 * mm) -> list:
    """A heading that starts a new page only when less than `need` of the page is left, so it is never stranded at the
    bottom and a table that follows may still split between its rows instead of jumping whole and leaving a hole."""
    return [CondPageBreak(need), _p(title, "h2")] + ([_p(sub, "h2sub")] if sub else [])


def _pair(left: list, right: list) -> Table:
    t = Table([[left, right]], colWidths=[HALF, HALF + 8 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), 8 * mm),
                           ("RIGHTPADDING", (1, 0), (1, 0), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _chart_block(title: str, sub: str, drawing: Drawing | None, empty: str) -> list:
    return [_p(title, "h3"), _p(sub, "h3sub"), drawing if drawing is not None else _p(empty, "note")]


def _tiles(a: dict, cur: str) -> Table:
    h, days = a["headline"], a["days"]
    m = lambda v: f"{cur} {_n(v)}".strip()
    ref = f"on the {days} days before" if days else None
    label = _period_label(days).lower()

    def tile(name: str, value: str, sub: str) -> list:
        return [_p(name, "label"), _p(value, "value"), _rich(sub, "small")]

    def metric(key: str, good_when: str, tail: str) -> str:
        words, color = _change(h[key]["change_pct"], good_when)
        return f"{_e(words, color, True)} {_e(tail, MUTED)}"

    mp = h["margin_pct"]
    if mp["change_points"] is None:
        margin_sub = _e("Kept from each sale after spending", MUTED)
    else:
        up = mp["change_points"] >= 0
        margin_sub = f"{_e(f'{'up' if up else 'down'} {abs(mp['change_points']):.1f} points', GOOD if up else BAD, True)} {_e(ref or '', MUTED)}"
    returned = a["customers"]["repeat_rate_pct"]
    cells = [
        tile(f"Sales, {label}", m(h["sales"]["current"]), metric("sales", "up", ref or "all your records")),
        tile(f"Spending, {label}", m(h["spending"]["current"]), metric("spending", "down", ref or "all your records")),
        tile(f"Profit, {label}", m(h["profit"]["current"]), metric("profit", "up", ref or "after recorded spending")),
        tile("Profit margin", "No sales" if mp["current"] is None else f"{mp['current']:.1f}%", margin_sub),
        tile("Average sale", m(h["average_sale"]["current"]), metric("average_sale", "up", f"{h['transactions']['current']:,} sales" + (f" {ref}" if ref else ""))),
        tile("Customers who bought", f"{h['customers']['current']:,}",
             metric("customers", "up", f"{returned:.0f}% of named customers came back" if returned is not None else (ref or ""))),
    ]
    gap, w = 3 * mm, (WIDTH - 2 * 3 * mm) / 3
    spacer = [""] * 5
    t = Table([[cells[0], "", cells[1], "", cells[2]], spacer, [cells[3], "", cells[4], "", cells[5]]], colWidths=[w, gap, w, gap, w],
              rowHeights=[None, gap, None])
    t.setStyle(TableStyle([("BACKGROUND", (c, r), (c, r), WASH) for r in (0, 2) for c in (0, 2, 4)]
                          + [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 7),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 0)]))
    return t


def _alerts_block(alerts: list[dict]) -> list:
    out = _section("Needs your attention")
    if not alerts:
        return out + [_p("No open alerts. Nothing in your records is outside its usual range.", "body")]
    rows = []
    for al in alerts[:3]:
        severity = str(al.get("severity", "")).lower()
        text = _t(al.get("what_happened") or "")
        rows.append([_rich(_e(severity.upper() or "ALERT", SEVERITY.get(severity, FAINT), True), "small"),
                     [_p(al.get("title", ""), "h3"), _p(text if len(text) <= 260 else text[:257].rstrip() + "...", "small")]])
    t = Table(rows, colWidths=[28 * mm, WIDTH - 28 * mm])
    t.setStyle(TableStyle(_BASE_STYLE[:-2] + [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID)]))
    return out + [t]


def _insights_block(insights: list[dict]) -> list:
    out = _section("What your records say", "Each line is worked out from the figures in this report, not guessed.")
    rows = [[_rich(_e("\u2022", TONE.get(i["tone"], FAINT), True), "body"), [_p(i["title"], "h3"), _p(i["text"], "small")]] for i in insights]
    t = Table(rows, colWidths=[6 * mm, WIDTH - 6 * mm])
    t.setStyle(TableStyle(_BASE_STYLE[:-2] + [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID)]))
    return out + [t]


def _rank_table(rows: list[tuple], top: float, bar_w: float) -> Table:
    """Name, bar, amount, share, change: one row per item; the bar's colour says which items matter most."""
    widths = [WIDTH * 0.30, bar_w, WIDTH * 0.15, WIDTH * 0.09, 0]
    widths[4] = WIDTH - sum(widths[:4])
    cells = [[_p(name, "cell"), _bar_cell(value, top, color, bar_w - 6), _p(amount, "cellr"), _p(share, "cellr"), _rich(change, "cellr")]
             for name, value, color, amount, share, change in rows]
    t = Table(cells, colWidths=widths)
    t.setStyle(TableStyle(_BASE_STYLE[:-2] + [("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID)]))
    return t


def _change_markup(pct, good_when: str) -> str:
    words, color = _change(pct, good_when)
    return _e(words, color, True)


def _money_table(head: list[str], rows: list[list], first: float, numeric: tuple) -> Table:
    rest = (WIDTH - first) / (len(head) - 1)
    return _table(head, rows, [first] + [rest] * (len(head) - 1), numeric)


def _story(a: dict, alerts: list[dict], business: str, cur: str, prepared: date) -> list:
    p = a["period"]
    m = lambda v: f"{cur} {_n(v)}".strip()
    pct = lambda v, digits=0: "-" if v is None else f"{v:.{digits}f}%"
    story: list = [_p("Analytics", "title"),
                   _p(f"{business}  |  {_day(p['first_day'])} to {_day(p['last_day'])}  |  Prepared {_day(prepared.isoformat())}", "meta"),
                   _p("Whole days on your business's clock" + (", today so far included" if p.get("includes_today") else ", ending yesterday") + ", from records that passed validation.", "note"), Spacer(1, 12), _tiles(a, cur)]
    story += _alerts_block(alerts)
    if a["insights"]:
        story += _insights_block(a["insights"])

    monthly = a["monthly"]
    story += _section("Sales, spending and profit by month", "The shape of the year. Profit is what is left after the spending you recorded.", need=60 * mm)
    story += [_Fit(lambda w, h: _monthly_chart(monthly, w, h), WIDTH, 110, 170), Spacer(1, 6),
              _money_table(["Month", f"Sales ({cur})", f"Spending ({cur})", f"Profit ({cur})", "Margin"],
                           [[r["month"][:7] + (" (so far)" if r["partial"] else ""), _n(r["sales"]), _n(r["spending"]), _n(r["profit"]), pct(r["margin_pct"])] for r in monthly],
                           WIDTH * 0.28, (1, 2, 3, 4))]

    fc = a["forecast"]
    has_fc = len(fc["weeks"]) >= 2
    fc_sub = (f"A straight-line view of your last {len(fc['weeks'])} complete weeks." + ("" if fc["reliable"] else " Weekly sales swing a lot, so read the range, not the middle line.")
              if fc["projection"] else fc.get("note", ""))
    half_w = HALF - 2 * mm
    story += [Spacer(1, 14), _pair(
        _chart_block("How much of each sale you keep", "Profit margin by month. A falling line means costs are growing faster than sales.", _margin_chart(monthly, half_w, 110), ""),
        _chart_block("Where weekly sales are heading", fc_sub, _forecast_chart(fc, half_w, 110) if has_fc else None,
                     "Needs a few complete weeks of sales before it can draw anything."))]
    if fc["r_squared"] is not None:
        story.append(_p(f"How well a straight line fits the weekly sales: {fc['r_squared'] * 100:.0f}%. Weekly sales vary about {fc['weekly_noise_pct'] or 0:.0f}% around their average.", "note"))

    if a["branches"]:
        story += _section("Branch by branch", "Sales and recorded spending side by side, with what each branch leaves you. Spending with no branch recorded is not counted here.")
        story.append(_money_table(["Branch", f"Sales ({cur})", f"Spending ({cur})", f"Profit ({cur})", "Margin", "Share", "Sales vs before"],
                                  [[b["name"], _n(b["sales"]), _n(b["spending"]), _n(b["profit"]), pct(b["margin_pct"]), pct(b["share_pct"]),
                                    _rich(_change_markup(b["sales_change_pct"], "up"), "cellr")] for b in a["branches"]], WIDTH * 0.22, (1, 2, 3, 4, 5, 6)))

    prod = a["products"]
    story += _section("What sells", f"{prod['products_for_80pct']} of {prod['count']} products make 80% of sales. Those are shaded teal." if prod["products_for_80pct"] else "Products by sales.")
    if prod["top"]:
        story.append(_rank_table([(x["name"], x["amount"], SALES if prod["products_for_80pct"] is not None and i < prod["products_for_80pct"] else BAR_OFF, m(x["amount"]),
                                   pct(x["share_pct"]), _change_markup(x["change_pct"], "up") if x["change_pct"] is not None else "") for i, x in enumerate(prod["top"])],
                                 prod["top"][0]["amount"], WIDTH * 0.30))
        if prod["others"]["count"]:
            story.append(_p(f"And {prod['others']['count']} more products, {pct(prod['others']['share_pct'])} of sales together.", "note"))
    else:
        story.append(_p("No product is recorded on your sales.", "body"))

    sp = a["spending"]
    story += _section("Where the money goes", f"{m(sp['total'])} spent. The words compare each category with the period before.")
    if sp["categories"]:
        story.append(_rank_table([(x["name"], x["amount"], SPENDING, m(x["amount"]), pct(x["share_pct"]),
                                   _change_markup(x["change_pct"], "down") if x["previous"] is not None else "") for x in sp["categories"]],
                                 sp["categories"][0]["amount"], WIDTH * 0.30))
    else:
        story.append(_p("No spending recorded in this period.", "body"))

    cu = a["customers"]
    facts = [("Named customers", f"{cu['named_count']:,}"), ("New in this period", "-" if cu["new_customers"] is None else f"{cu['new_customers']:,}"),
             ("Returning", "-" if cu["returning_customers"] is None else f"{cu['returning_customers']:,}"), ("Came back", pct(cu["repeat_rate_pct"])),
             ("Biggest customer's share of sales", pct(cu["top_customer_share_pct"])), ("Top five customers' share", pct(cu["top5_share_pct"])),
             ("Walk-in buyers' share", pct(cu["generic_share_pct"]))]
    facts_table = Table([[_p(k, "cell"), _p(v, "cellr")] for k, v in facts], colWidths=[HALF - 22 * mm, 20 * mm])
    facts_table.setStyle(TableStyle(_BASE_STYLE[:-2] + [("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID)]))
    story += [Spacer(1, 14), _pair(
        _chart_block("Which days carry the business", "Average sales on each weekday of the period, counting closed days as zero.",
                     _weekday_chart(a["weekdays"], half_w, 110) if a["weekdays"] else None, "Needs at least two weeks in the period to compare weekdays. Choose a longer period."),
        [_p("Customers at a glance", "h3"), _p("Named customers only; walk-in buyers are not one customer.", "h3sub"), facts_table])]

    story += _section("Who buys", f"Your biggest named customer is {cu['top_customer_share_pct']:.0f}% of sales; the top five are {cu['top5_share_pct'] or 0:.0f}%."
                      if cu["top_customer_share_pct"] is not None else "Customers by sales.")
    if cu["top"]:
        story.append(_rank_table([(x["name"] + (" (walk-in)" if x["generic"] else f", {x['orders']} order{'' if x['orders'] == 1 else 's'}"), x["amount"],
                                   BAR_OFF if x["generic"] else SALES, m(x["amount"]), pct(x["share_pct"]), "") for x in cu["top"]], cu["top"][0]["amount"], WIDTH * 0.30))
    else:
        story.append(_p("No customer is recorded on your sales.", "body"))

    story += _section("Every day, the last 26 weeks", "Darker days sold more. Quiet stretches and gaps in your records show up at once.", need=50 * mm)
    story += [_Fit(lambda w, h: _heatmap(a["daily"], p["last_day"], w, h), WIDTH, 95, 135),
              Spacer(1, 12),
              _p("Profit here is sales minus the spending you recorded, so stock purchases count as spending when they are paid. Records that failed "
                 "validation, and duplicates of a record already counted, are left out of every figure.", "note")]
    return story


def _numbered_canvas(left_text: str):
    """A canvas that knows the page count, so each footer can say 'Page 2 of 5'."""

    class Numbered(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved: list[dict] = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFillColor(SALES)
                self.rect(0, PAGE_H - 3 * mm, PAGE_W, 3 * mm, stroke=0, fill=1)
                self.setStrokeColor(GRID)
                self.setLineWidth(0.5)
                self.line(MARGIN_X, 11 * mm, PAGE_W - MARGIN_X, 11 * mm)
                self.setFont(FONT, 7.5)
                self.setFillColor(FAINT)
                self.drawString(MARGIN_X, 7 * mm, left_text)
                self.drawRightString(PAGE_W - MARGIN_X, 7 * mm, f"Page {self._pageNumber} of {total}")
                super().showPage()
            super().save()

    return Numbered


def build_analytics_pdf(a: dict, alerts: list[dict], business: str, currency: str, prepared: date) -> bytes:
    """The whole report as PDF bytes. `a` is build_analytics()'s answer; `alerts` the open alerts, most important first."""
    business = _t(business)
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM,
                          title=f"Analytics, {business}", subject=f"{_day(a['period']['first_day'])} to {_day(a['period']['last_day'])}", creator="Business Reality Engine")
    doc.addPageTemplates([PageTemplate(id="page", frames=[Frame(MARGIN_X, MARGIN_BOTTOM, WIDTH, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="body", leftPadding=0,
                                                                rightPadding=0, topPadding=0, bottomPadding=0)])])
    doc.build(_story(a, alerts, business, _t(currency), prepared),
              canvasmaker=_numbered_canvas(f"{business}  |  Analytics  |  {_day(a['period']['first_day'])} to {_day(a['period']['last_day'])}"))
    return buf.getvalue()
