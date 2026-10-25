"""The Analytics PDF: right content, a layout that flows (no half-empty pages), safe for odd data, and tenant-scoped."""
import datetime as dt
import io
import random

import pytest
from pypdf import PdfReader

from app.core.analytics import build_analytics
from app.core.analytics_pdf import MARGIN_BOTTOM, PAGE_H, build_analytics_pdf
from app.core.db import SessionLocal
from tests.conftest import _make_tenant
from tests.test_analytics import SPEC, _seed
from tests.test_block_c import make_owner

TODAY = dt.date(2026, 10, 25)
ALERTS = [{"severity": "high", "title": "Spending on Transport rose 34%", "what_happened": "Transport reached KES 182,400 against a usual KES 136,000."},
          {"severity": "opportunity", "title": "Tea sales are running ahead", "what_happened": "Tea sold KES 94,300 this week."}]


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


def _metric(cur, prev=None):
    return {"current": cur, "previous": prev, "change_pct": None if not prev else round((cur - prev) / prev * 100, 1)}


def _full(rng: random.Random, **override) -> dict:
    """A rich analytics answer, shaped exactly like build_analytics(), without a database."""
    months = [{"month": f"2025-{m:02d}-01" if m > 10 else f"2026-{m:02d}-01", "sales": 3e6 + rng.random() * 1e6, "spending": 6e5 + rng.random() * 2e5,
               "profit": 2.4e6, "margin_pct": 78.0, "partial": False} for m in (11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]
    weeks = [{"week_start": (dt.date(2026, 8, 3) + dt.timedelta(weeks=i)).isoformat(), "sales": 8e5 + rng.random() * 2e5, "spending": 1.5e5} for i in range(12)]
    names = lambda stem, n: [f"{stem} {i}" for i in range(n)]
    dim = lambda n: [{"name": x, "amount": 9e5 - i * 3e4, "count": 10, "quantity": 10, "previous": 8e5, "change_pct": 5.0 - i, "share_pct": 8.0 - i / 4,
                      "cumulative_pct": 20.0 + i} for i, x in enumerate(names("Item", n))]
    a = {
        "period": {"days": 90, "all_time": False, "first_day": "2026-07-28", "last_day": "2026-10-24", "timezone": "Africa/Nairobi"}, "days": 90,
        "headline": {"sales": _metric(1.05e7, 1e7), "spending": _metric(1.9e6, 1.8e6), "profit": _metric(8.6e6, 8.2e6), "average_sale": _metric(11270, 10900),
                     "transactions": _metric(937, 900), "customers": _metric(41, 40),
                     "margin_pct": {"current": 81.4, "previous": 82.0, "change_points": -0.6}, "spending_ratio_pct": {"current": 18.0, "previous": 18.0}},
        "monthly": months,
        "branches": [{"name": n, "sales": 3e6, "spending": 4e5, "profit": 2.6e6, "margin_pct": 86.0, "share_pct": 25.0, "transactions": 200, "average_sale": 15000,
                      "sales_change_pct": 3.0} for n in names("Branch", 4)],
        "products": {"top": dim(20), "count": 30, "others": {"count": 10, "amount": 1e6, "share_pct": 7.0}, "products_for_80pct": 11},
        "spending": {"categories": dim(7), "total": 1.9e6},
        "weekdays": [{"day": d, "average_sales": 1e5 + i * 1e4, "days": 13} for i, d in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])],
        "customers": {"top": [{"name": f"Customer {i}", "amount": 3e5 - i * 1e4, "orders": 20, "share_pct": 3.0, "generic": i == 0} for i in range(10)], "named_count": 40,
                      "new_customers": 3, "returning_customers": 37, "repeat_rate_pct": 92.0, "top_customer_share_pct": 4.0, "top5_share_pct": 15.0, "generic_share_pct": 30.0},
        "daily": [{"date": (dt.date(2026, 10, 24) - dt.timedelta(days=i)).isoformat(), "sales": rng.random() * 2e5, "spending": rng.random() * 3e4} for i in range(182)],
        "forecast": {"weeks": weeks, "projection": [{"week_start": (dt.date(2026, 10, 26) + dt.timedelta(weeks=i)).isoformat(), "expected": 8.5e5, "low": 7e5, "high": 1e6} for i in range(4)],
                     "method": "linear", "r_squared": 0.12, "reliable": False, "weekly_noise_pct": 11.0},
        "insights": [{"tone": t, "title": f"Insight {i}", "text": "A sentence built from the figures. " * 2} for i, t in enumerate(["good", "warn", "bad", "info", "good", "info"])],
    }
    a.update(override)
    return a


def _empty() -> dict:
    zero = _metric(0.0)
    return _full(random.Random(1), headline={"sales": zero, "spending": zero, "profit": zero, "average_sale": zero, "transactions": zero, "customers": zero,
                                             "margin_pct": {"current": None, "previous": None, "change_points": None}, "spending_ratio_pct": {"current": None, "previous": None}},
                 monthly=[{"month": "2026-10-01", "sales": 0.0, "spending": 0.0, "profit": 0.0, "margin_pct": None, "partial": True}], branches=[],
                 products={"top": [], "count": 0, "others": {"count": 0, "amount": 0, "share_pct": None}, "products_for_80pct": None}, spending={"categories": [], "total": 0},
                 weekdays=[], customers={"top": [], "named_count": 0, "new_customers": None, "returning_customers": None, "repeat_rate_pct": None, "top_customer_share_pct": None,
                                         "top5_share_pct": None, "generic_share_pct": None},
                 daily=[], forecast={"weeks": [], "projection": [], "method": "linear", "r_squared": None, "reliable": False, "note": "Needs a few complete weeks of sales."}, insights=[])


def _pages(pdf: bytes) -> list[PdfReader]:
    assert pdf.startswith(b"%PDF")
    return PdfReader(io.BytesIO(pdf)).pages


def _lowest_body_text(page) -> float:
    """The lowest y on the page that holds body text (the footer sits lower and is skipped)."""
    ys: list[float] = []

    def seen(text, cm, tm, font, size):
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]                # the text's own position, moved into page coordinates
        if text.strip() and y > 40:
            ys.append(y)

    page.extract_text(visitor_text=seen)
    return min(ys)


def test_report_carries_the_business_period_figures_alerts_and_insights():
    pdf = build_analytics_pdf(_full(random.Random(3)), ALERTS, "DericBI", "KES", TODAY)
    text = "\n".join(p.extract_text() for p in _pages(pdf))
    for expected in ("Analytics", "DericBI", "28 Jul 2026 to 24 Oct 2026", "KES 10,500,000", "Needs your attention", "Spending on Transport rose 34%",
                     "What your records say", "Insight 3", "Branch by branch", "What sells", "Where the money goes", "Who buys", "Page 1 of"):
        assert expected in text, expected


def test_pages_flow_without_big_empty_areas_and_none_is_blank():
    pages = _pages(build_analytics_pdf(_full(random.Random(5)), ALERTS, "DericBI", "KES", TODAY))
    assert len(pages) >= 3
    for page in pages:
        assert page.extract_text().strip()
    for page in pages[:-1]:                                    # every page but the last must be filled to within a quarter of a page of its foot
        assert _lowest_body_text(page) - MARGIN_BOTTOM <= 0.25 * PAGE_H


def test_a_business_with_no_records_still_gets_a_clean_report():
    pages = _pages(build_analytics_pdf(_empty(), [], "Quiet Shop", "KES", TODAY))
    text = "\n".join(p.extract_text() for p in pages)
    assert "No open alerts" in text and "No product is recorded" in text and "No spending recorded" in text and "No customer is recorded" in text


def test_names_the_built_in_fonts_cannot_draw_never_break_the_report():
    a = _full(random.Random(7))
    a["branches"][0]["name"] = "Ma\u00fcua \u5e97 <b>&</b>"
    a["headline"]["profit"] = _metric(-5e5, 1e5)
    pdf = build_analytics_pdf(a, [{"severity": "unheard-of", "title": "\u5e97 <i>x</i>", "what_happened": "y" * 900}], "Caf\u00e9 \u5e97 & Sons", "\u20a6", TODAY)
    assert len(_pages(pdf)) >= 3


def test_real_figures_from_the_records_reach_the_pdf(db):
    t = _make_tenant("pdfreal"); _seed(db, t, SPEC)
    a = build_analytics(db, t, 30)
    text = "\n".join(p.extract_text() for p in _pages(build_analytics_pdf(a, [], "Real Co", "KES", TODAY)))
    assert "KES 10,000" in text and "Meru" in text and "Maua" in text and "Sugar" in text and "Rent" in text and "Ann" in text


def test_pdf_endpoint_needs_a_sign_in_is_tenant_scoped_and_downloads_a_pdf(client, db):
    t, h = make_owner(client, "pdfapi"); _seed(db, t, SPEC)
    assert client.get(f"/api/business/analytics.pdf?tenant_id={t}&days=30").status_code in (401, 403)
    ok = client.get(f"/api/business/analytics.pdf?tenant_id={t}&days=30", headers=h)
    assert ok.status_code == 200 and ok.headers["content-type"] == "application/pdf" and ok.content.startswith(b"%PDF")
    assert ok.headers["content-disposition"].startswith('attachment; filename="analytics-') and ok.headers["cache-control"] == "no-store"
    other = _make_tenant("pdfother")
    assert client.get(f"/api/business/analytics.pdf?tenant_id={other}&days=30", headers=h).status_code in (401, 403, 404)
