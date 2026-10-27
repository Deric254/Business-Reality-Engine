"""Ingestion benchmark (in-process, real Postgres). Usage:
    python scripts/bench_ingest.py [events] [distinct_customers]
Reports events/sec for the per-event path and, when available, the bulk path.
Creates (and leaves) a throwaway tenant; run against a dev database only."""
import os, sys, time, random, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.db import SessionLocal
from app.models.core import Tenant, Connector
from app.core import ingestion
from app.core.ingestion import NormalizedEvent
from app.core.clock import utcnow

FIRST = ["John","Jane","Mary","Peter","Grace","James","Faith","David","Ann","Paul","Lucy","Brian","Mercy","Kevin","Joy","Samuel","Esther","Daniel","Ruth","Moses"]
LAST = ["Mwangi","Otieno","Kamau","Wanjiru","Njoroge","Achieng","Kiprop","Mutua","Chebet","Omondi","Karanja","Wafula","Naliaka","Kariuki","Mbugua"]

def make(n, customers, tenant, conn, tag):
    rnd = random.Random(7)
    names = [f"{rnd.choice(FIRST)} {rnd.choice(LAST)} {i}" for i in range(customers)]
    base = utcnow() - dt.timedelta(days=5)
    return [NormalizedEvent(tenant_id=tenant, connector_id=conn, source="bench", entity="sale",
            entity_id=f"{tag}-{i}", event_type="sale", timestamp=base + dt.timedelta(seconds=i),
            amount=100 + (i % 50), customer_id=names[i % customers]) for i in range(n)]

def setup(db):
    t = Tenant(name="bench"); db.add(t); db.commit()
    c = Connector(tenant_id=t.id, name="b", connector_type="webhook", config={}); db.add(c); db.commit()
    return t.id, c.id

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    customers = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    db = SessionLocal()
    tid, cid = setup(db)
    if "--bulk-only" not in sys.argv:
        evs = make(n, customers, tid, cid, "a")
        t0 = time.time()
        for e in evs: ingestion.ingest_normalized_event(db, e)
        dt1 = time.time() - t0
        print(f"per-event path: {n} events, {customers} customers: {n/dt1:,.0f} events/s ({dt1:.1f}s)")
    if hasattr(ingestion, "ingest_events_bulk"):
        tid, cid = setup(db)
        evs = make(n, customers, tid, cid, "b")
        t0 = time.time()
        res = ingestion.ingest_events_bulk(db, evs)
        dt2 = time.time() - t0
        print(f"bulk path:      {n} events, {customers} customers: {n/dt2:,.0f} events/s ({dt2:.1f}s)")
        t0 = time.time(); res = ingestion.ingest_events_bulk(db, evs); dt3 = time.time() - t0
        print(f"bulk re-send (all duplicates): {n/dt3:,.0f} events/s; duplicates={sum(r['duplicate'] for r in res)}")

if __name__ == "__main__":
    main()
