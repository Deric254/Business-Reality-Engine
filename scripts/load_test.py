"""HTTP load test against a running API. Usage:
   python scripts/load_test.py BASE_URL EMAIL PASSWORD TENANT_ID CONNECTOR_ID SERVICE_KEY [seconds] [concurrency]
Mixes authenticated reads (alerts, KPIs) with service-key batch ingestion
(50 events/request, every id unique so nothing is deduplicated away).
Run the API with RATE_LIMIT_GENERAL_PER_MIN raised, or every request past 300/min is a 429."""
import asyncio, sys, time, uuid, datetime as dt
import httpx

base, email, pw, tenant, conn, skey = sys.argv[1:7]
secs = int(sys.argv[7]) if len(sys.argv) > 7 else 15
conc = int(sys.argv[8]) if len(sys.argv) > 8 else 64

def batch():
    ts = (dt.datetime.utcnow() - dt.timedelta(hours=1)).isoformat()
    return [{"tenant_id": tenant, "connector_id": conn, "source": "load", "entity": "sale", "entity_id": uuid.uuid4().hex,
             "event_type": "sale", "timestamp": ts, "amount": 10.0, "customer_id": f"lc{i % 200}"} for i in range(50)]

async def main():
    lat = {"read": [], "ingest": []}; codes = {}; events = 0
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=httpx.Limits(max_connections=conc)) as c:
        tok = (await c.post("/api/auth/login", json={"email": email, "password": pw})).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        end = time.time() + secs
        async def worker(n):
            nonlocal events
            i = 0
            while time.time() < end:
                i += 1; t = time.time()
                try:
                    if (i + n) % 4 == 0:
                        kind = "ingest"
                        r = await c.post("/api/events/ingest/batch", json=batch(), headers={"x-api-key": skey})
                        if r.status_code == 200: events += 50
                    elif (i + n) % 4 == 1:
                        kind = "read"; r = await c.get(f"/api/questions/kpis?tenant_id={tenant}", headers=H)
                    else:
                        kind = "read"; r = await c.get(f"/api/alerts/?tenant_id={tenant}", headers=H)
                except httpx.TransportError as e:   # dropped connection / timeout: count it, keep going
                    codes[type(e).__name__] = codes.get(type(e).__name__, 0) + 1
                    continue
                lat[kind].append(time.time() - t); codes[r.status_code] = codes.get(r.status_code, 0) + 1
        t0 = time.time()
        await asyncio.gather(*[worker(n) for n in range(conc)])
        el = time.time() - t0
    tot = sum(codes.values())
    print(f"{tot} requests in {el:.1f}s = {tot/el:,.0f} req/s | status {codes} | ingested {events:,} events = {events/el:,.0f} ev/s")
    for k, v in lat.items():
        if v:
            v.sort(); print(f"{k}: n={len(v)} p50={v[len(v)//2]*1000:.0f}ms p95={v[int(len(v)*.95)]*1000:.0f}ms p99={v[int(len(v)*.99)]*1000:.0f}ms")
asyncio.run(main())
