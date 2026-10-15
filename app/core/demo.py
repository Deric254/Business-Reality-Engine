"""Sample data: about 100 days of realistic sales and expenses for a small shop ending in a sales slump, so a new owner can
see alerts, causes and the dashboard working before connecting anything real. Lives on one clearly named connector and can
be removed again, but only while it is the ONLY data in the business (real data is never touched)."""
import random
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.ingestion import NormalizedEvent, ingest_events_bulk
from app.core.detectors import run_tenant_cycle
from app.models.config import MonitoredMetric
from app.models.core import BusinessEvent, Connector

SAMPLE_NAME = "Sample data"


def _sample_connector(db: Session, tenant_id: str):
    return db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.name == SAMPLE_NAME,
                                      Connector.connector_type == "manual").first()


def load_sample(db: Session, tenant_id: str, days: int = 100) -> dict:
    if db.query(BusinessEvent.id).filter(BusinessEvent.tenant_id == tenant_id).first():
        raise HTTPException(409, "This business already has data; sample data can only be added to an empty business")
    conn = Connector(tenant_id=tenant_id, name=SAMPLE_NAME, connector_type="manual", expected_frequency="slow", is_active=True, config={})
    db.add(conn); db.commit(); db.refresh(conn)
    rnd = random.Random(11)
    now = datetime.utcnow()
    shops = ["Meru Town", "Maua", "Nkubu"]
    products = ["Sugar 2kg", "Cooking oil", "Maize flour", "Tea leaves", "Rice 5kg"]
    custs = [f"Customer {i}" for i in range(1, 31)]
    events = []
    for d in range(days, 0, -1):
        day = now - timedelta(days=d)
        weekend = day.weekday() >= 5
        slump = 0.6 if d <= 9 else 1.0
        base = rnd.uniform(14, 22) * (1.25 if weekend else 1.0) * slump
        for _ in range(int(base)):
            shop = rnd.choice(shops) if not (d <= 9 and rnd.random() < 0.55) else "Maua"      # the slump is concentrated at Maua's rivals
            events.append(NormalizedEvent(
                tenant_id=tenant_id, connector_id=conn.id, source=SAMPLE_NAME, entity="sale", event_type="sale.created",
                entity_id=f"S{uuid.uuid4().hex[:10]}", timestamp=day.replace(hour=rnd.randint(8, 19), minute=rnd.randint(0, 59), second=0, microsecond=0),
                amount=round(rnd.uniform(300, 2500), 2), quantity=rnd.randint(1, 6), currency="KES",
                location=shop, product_id=rnd.choice(products), customer_id=rnd.choice(custs)))
        for _ in range(rnd.randint(2, 4)):
            events.append(NormalizedEvent(
                tenant_id=tenant_id, connector_id=conn.id, source=SAMPLE_NAME, entity="expense", event_type="expense.created",
                entity_id=f"E{uuid.uuid4().hex[:10]}", timestamp=day.replace(hour=rnd.randint(8, 19), minute=0, second=0, microsecond=0),
                amount=round(rnd.uniform(500, 2200), 2), currency="KES", location=rnd.choice(shops)))
    ingest_events_bulk(db, events)
    metrics = [{"entity": m.entity, "field": m.field, "period_days": m.period_days, "baseline_periods": m.baseline_periods,
                "deterioration_threshold": float(m.threshold_pct) / 100, "direction": m.direction}
               for m in db.query(MonitoredMetric).filter(MonitoredMetric.tenant_id == tenant_id, MonitoredMetric.enabled == True)]  # noqa: E712
    alerts = run_tenant_cycle(db, tenant_id, metrics or [{"entity": "sale", "field": "amount", "direction": "drop"}])["alerts"]
    return {"events": len(events), "alerts": len(alerts), "connector_id": conn.id}


def clear_sample(db: Session, tenant_id: str) -> dict:
    conn = _sample_connector(db, tenant_id)
    if conn is None:
        raise HTTPException(404, "There is no sample data to remove")
    other = db.query(BusinessEvent.id).filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.connector_id != conn.id).first()
    if other:
        raise HTTPException(409, "Real data has been added, so sample data can't be removed automatically")
    p = {"t": tenant_id, "c": conn.id}
    for sql in (
        "DELETE FROM notification_deliveries WHERE tenant_id = :t",
        "DELETE FROM cases WHERE tenant_id = :t",
        "DELETE FROM alerts WHERE tenant_id = :t",
        "DELETE FROM business_events WHERE tenant_id = :t",
        "DELETE FROM entity_aliases WHERE entity_id IN (SELECT id FROM resolved_entities WHERE tenant_id = :t)",
        "DELETE FROM resolved_entities WHERE tenant_id = :t",
        "DELETE FROM data_coverage WHERE tenant_id = :t",
        "DELETE FROM connectors WHERE id = :c",
    ):
        db.execute(text(sql), p)
    db.commit()
    return {"removed": True}
