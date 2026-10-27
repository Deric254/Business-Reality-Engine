"""Sample businesses: about four months of believable activity for a business that has a problem hiding in it, so a new
owner (or a prospect) can see alerts, causes and the dashboard working before anything real is connected.

Each sample is one clearly named connector holding every one of its records. It can be removed again, but only while it is
the ONLY data in the business: real data is never touched. Times are written on the business's own clock and every sample
ends yesterday, so nothing is in the future and the last complete day is a normal one.

What each sample hides, and the alert that finds it:
  duka      a shop whose Maua branch lost most of its sales in the last two weeks      -> branch decline
  pharmacy  stock buying that has almost doubled while takings stayed flat             -> cost spike, margin squeeze
  workshop  half of all invoices come from one customer, and one huge equipment bill   -> customer concentration, large payment
"""
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cache import invalidate_tenant
from app.core.detectors import run_tenant_cycle
from app.core.ingestion import NormalizedEvent, ingest_events_bulk
from app.core.tenant_time import tenant_zone
from app.models.config import MonitoredMetric
from app.models.core import BusinessEvent, Connector

SAMPLE_NAME = "Sample data"
DAYS = 125                 # the detectors compare the last 30 days with the three 30-day windows before them


@dataclass(frozen=True)
class SampleKind:
    id: str
    title: str
    business_name: str
    blurb: str


KINDS = [
    SampleKind("duka", "Neighbourhood shop", "Karibu Duka", "Three branches. One of them has gone quiet in the last two weeks."),
    SampleKind("pharmacy", "Pharmacy", "Afya Pharmacy", "Takings are steady, but the stock bill has almost doubled."),
    SampleKind("workshop", "Workshop (business customers)", "Imani Wire Works", "Half the invoices come from one customer, and a very large bill has just been paid."),
]
_FIRST = ["Wanjiru", "Kamau", "Achieng", "Otieno", "Njeri", "Mwangi", "Akinyi", "Kiprop", "Atieno", "Mutua", "Wambui", "Kariuki"]
_LAST = ["Muriuki", "Odhiambo", "Kimani", "Wekesa", "Chebet", "Mbugua"]
_PEOPLE = [f"{f} {l}" for l in _LAST for f in _FIRST]            # 72 distinct, believable names


def _builder_duka(rnd: random.Random, sale: Callable, expense: Callable, today: datetime) -> None:
    shops = ["Meru Town", "Maua", "Nkubu"]
    products = ["Sugar 2kg", "Cooking oil", "Maize flour", "Tea leaves", "Rice 5kg"]
    categories = ["Stock purchases", "Transport", "Wages", "Electricity"]
    for d in range(DAYS, 0, -1):
        weekend = (today - timedelta(days=d)).weekday() >= 5
        for shop in shops:
            n = rnd.uniform(4.5, 7.5) * (1.25 if weekend else 1.0)
            if shop == "Maua" and d <= 14:
                n *= 0.3                                           # the hidden problem
            for _ in range(int(n)):
                sale(d, rnd.randint(8, 19), rnd.randint(0, 59), round(rnd.uniform(300, 2500), 2), location=shop,
                     product_id=rnd.choice(products), customer_id=rnd.choice(_PEOPLE[:30]), quantity=rnd.randint(1, 6))
        for _ in range(rnd.randint(2, 4)):
            expense(d, rnd.randint(8, 19), 0, round(rnd.uniform(500, 2200), 2), location=rnd.choice(shops), product_id=rnd.choice(categories))


def _builder_pharmacy(rnd: random.Random, sale: Callable, expense: Callable, today: datetime) -> None:
    items = ["Painkillers", "Antibiotics", "Vitamins", "Cough syrup", "Baby products", "First aid"]
    others = [("Wages", 4200), ("Rent", 3000), ("Electricity", 900), ("Licences", 400)]
    for d in range(DAYS, 0, -1):
        for _ in range(rnd.randint(24, 34)):
            sale(d, rnd.randint(8, 20), rnd.randint(0, 59), round(rnd.uniform(150, 1800), 2), location="Main shop",
                 product_id=rnd.choice(items), customer_id=rnd.choice(_PEOPLE), quantity=rnd.randint(1, 4))
        stock_factor = 1.8 if d <= 30 else 1.0                     # the hidden problem
        for _ in range(rnd.randint(1, 2)):
            expense(d, rnd.randint(8, 12), 0, round(rnd.uniform(8000, 13000) * stock_factor, 2), location="Main shop", product_id="Stock purchases")
        category, typical = rnd.choice(others)
        expense(d, 15, 0, round(typical * rnd.uniform(0.7, 1.3), 2), location="Main shop", product_id=category)


def _builder_workshop(rnd: random.Random, sale: Callable, expense: Callable, today: datetime) -> None:
    clients = ["Kamau Hardware", "Mwangi Builders", "Otieno Fencing", "Chebet Farm Supplies", "Njeri Agrovet", "Akinyi Contractors",
               "Mutua Steel Mart", "Wambui Stores", "Kariuki Poultry", "Wekesa Timber"]
    for d in range(DAYS, 0, -1):
        if (today - timedelta(days=d)).weekday() == 6:
            continue                                               # closed on Sundays
        for _ in range(rnd.randint(2, 5)):
            client = "Kamau Hardware" if rnd.random() < 0.55 else rnd.choice(clients[1:])      # the hidden problem
            sale(d, rnd.randint(8, 17), rnd.randint(0, 59), round(rnd.uniform(6000, 60000), 2), location="Main workshop",
                 product_id=rnd.choice(["Fencing wire", "Binding wire", "Nails", "Mesh panels"]), customer_id=client, quantity=rnd.randint(5, 80))
        for _ in range(rnd.randint(1, 2)):
            expense(d, rnd.randint(8, 17), 0, round(rnd.uniform(20000, 45000), 2), location="Main workshop", product_id="Raw materials")
        expense(d, 16, 0, round(rnd.uniform(3000, 9000), 2), location="Main workshop", product_id=rnd.choice(["Wages", "Electricity", "Transport"]))
    expense(3, 11, 0, 650000.0, location="Main workshop", product_id="Equipment purchase")           # the hidden bill


_BUILDERS = {"duka": (_builder_duka, 11), "pharmacy": (_builder_pharmacy, 12), "workshop": (_builder_workshop, 13)}


def kind_by_id(kind: str) -> SampleKind:
    for k in KINDS:
        if k.id == kind:
            return k
    raise HTTPException(400, f"Unknown sample '{kind}'. Choose one of: {', '.join(k.id for k in KINDS)}")


def _sample_connector(db: Session, tenant_id: str) -> Optional[Connector]:
    return db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.name == SAMPLE_NAME,
                                      Connector.connector_type == "manual").first()


def has_sample(db: Session, tenant_id: str) -> bool:
    return _sample_connector(db, tenant_id) is not None


def load_sample(db: Session, tenant_id: str, kind: str = "duka") -> dict:
    spec = kind_by_id(kind)
    if db.query(BusinessEvent.id).filter(BusinessEvent.tenant_id == tenant_id).first():
        raise HTTPException(409, "This business already has data; sample data can only be added to an empty business")
    conn = Connector(tenant_id=tenant_id, name=SAMPLE_NAME, connector_type="manual", expected_frequency="slow", is_active=True, config={})
    db.add(conn); db.commit(); db.refresh(conn)
    today = datetime.now(tenant_zone(db, tenant_id))
    events: list[NormalizedEvent] = []

    def add(entity: str):
        def make(days_ago: int, hour: int, minute: int, amount: float, **fields):
            events.append(NormalizedEvent(
                tenant_id=tenant_id, connector_id=conn.id, source=SAMPLE_NAME, entity=entity, event_type=f"{entity}.created",
                entity_id=f"{entity[0].upper()}{len(events) + 1:06d}", currency="KES", amount=amount,
                timestamp=(today - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0, microsecond=0), **fields))
        return make

    builder, seed = _BUILDERS[spec.id]
    builder(random.Random(seed), add("sale"), add("expense"), today)
    ingest_events_bulk(db, events)
    metrics = [{"entity": m.entity, "field": m.field, "period_days": m.period_days, "baseline_periods": m.baseline_periods,
                "deterioration_threshold": float(m.threshold_pct) / 100, "direction": m.direction}
               for m in db.query(MonitoredMetric).filter(MonitoredMetric.tenant_id == tenant_id, MonitoredMetric.enabled == True)]  # noqa: E712
    alerts = run_tenant_cycle(db, tenant_id, metrics or [{"entity": "sale", "field": "amount", "direction": "drop"}])["alerts"]
    return {"kind": spec.id, "events": len(events), "alerts": len(alerts), "connector_id": conn.id}


def remove_sample(db: Session, tenant_id: str) -> dict:
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
    invalidate_tenant(tenant_id, detect=False)          # nothing cached from the sample may outlive it
    return {"removed": True}
