"""The one-off payment note names the business's own day, not the UTC one (stored times are naive UTC)."""
import datetime as dt

from app.core.db import SessionLocal
from app.core.detection import _one_off_driver
from app.models.core import BusinessEvent, Connector, Tenant
from tests.conftest import _make_tenant


def test_late_evening_payment_is_named_on_the_next_local_day():
    db = SessionLocal()
    try:
        t = _make_tenant("oneoffday")
        db.get(Tenant, t).timezone = "Africa/Nairobi"; db.commit()
        c = Connector(tenant_id=t, name="B", connector_type="file", expected_frequency="periodic", is_active=True)
        db.add(c); db.commit()
        # 22:30 UTC on 9 Sep is 01:30 on 10 Sep in Nairobi
        for i, amt in enumerate([100, 100, 100, 100, 90000]):
            ts = dt.datetime(2026, 9, 9, 22, 30) if amt == 90000 else dt.datetime(2026, 9, 5 + i, 9, 0)
            db.add(BusinessEvent(tenant_id=t, connector_id=c.id, source="t", entity="expense", event_type="expense.x",
                                 timestamp=ts, amount=amt, validation_status="validated"))
        db.commit()
        out = _one_off_driver(db, t, "expense", "payment", True, dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 30),
                              dt.datetime(2026, 8, 1), dt.datetime(2026, 9, 1), 90400, 1000, lambda v: f"{v:,.0f}")
        assert out and "10 Sep" in out["evidence"] and out["record"]["date"] == "2026-09-10"
    finally:
        db.close()
