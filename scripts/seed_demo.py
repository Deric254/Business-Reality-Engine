"""Create a demo business with ~120 days of realistic activity, ending in a sales slump, so every screen has
something to show. Usage:
    python scripts/seed_demo.py --api http://localhost:8000 --admin-key $ADMIN_API_KEY
Prints the demo login. Safe to run repeatedly (each run makes a new business)."""
import argparse
import random
import uuid
from datetime import datetime, timedelta

import httpx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--admin-key", required=True)
    ap.add_argument("--days", type=int, default=120)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, timeout=60)
    email, pw = f"demo-{uuid.uuid4().hex[:6]}@example.com", "demo-password-1"
    t = c.post("/api/system/tenants", headers={"x-admin-key": a.admin_key},
               json={"name": "Demo Duka", "owner_email": email, "owner_password": pw}).json()
    tid = t["id"]
    tok = c.post("/api/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    conn = c.post("/api/connectors/", headers=h, json={"tenant_id": tid, "name": "Till", "connector_type": "webhook",
                                                        "expected_frequency": "realtime"}).json()
    rnd = random.Random(7)
    now = datetime.utcnow()
    shops, products, custs = ["Meru Town", "Maua", "Nkubu"], ["Sugar 2kg", "Cooking oil", "Maize flour", "Tea leaves"], [f"Customer {i}" for i in range(1, 25)]
    events = []
    for d in range(a.days, 0, -1):
        day = now - timedelta(days=d)
        slump = 0.62 if d <= 10 else 1.0                      # the last 10 days drop ~38%
        for _ in range(int(rnd.uniform(14, 22) * slump)):
            events.append(dict(tenant_id=tid, connector_id=conn["id"], source="demo", entity="sale", event_type="sale.created",
                               entity_id=f"S{uuid.uuid4().hex[:10]}", timestamp=(day.replace(hour=rnd.randint(8, 19), minute=rnd.randint(0, 59))).isoformat(),
                               amount=round(rnd.uniform(300, 2500), 2), quantity=rnd.randint(1, 6), currency="KES",
                               location=rnd.choice(shops), product_id=rnd.choice(products), customer_id=rnd.choice(custs)))
        for _ in range(rnd.randint(2, 4)):
            events.append(dict(tenant_id=tid, connector_id=conn["id"], source="demo", entity="expense", event_type="expense.created",
                               entity_id=f"E{uuid.uuid4().hex[:10]}", timestamp=(day.replace(hour=rnd.randint(8, 19))).isoformat(),
                               amount=round(rnd.uniform(500, 2200), 2), currency="KES", location=rnd.choice(shops)))
    for i in range(0, len(events), 500):
        r = c.post("/api/events/ingest/batch", headers=h, json=events[i:i + 500]); r.raise_for_status()
    c.post("/api/detection/run/all", headers=h, json={"tenant_id": tid})
    print(f"Demo ready: {len(events)} events\n  login: {email}\n  password: {pw}\n  tenant: {tid}")


if __name__ == "__main__":
    main()
