"""Create a demo business on a running server, loaded with sample activity, from the command line.

    python scripts/seed_demo.py --api http://localhost:8000 --admin-key $ADMIN_API_KEY [--kind duka|pharmacy|workshop]

Prints the login. Each run makes a new business. The sample itself is built by the server (app/core/demo.py), the same one
behind "Want to see it working first?" on the Data page, so there is a single source of truth for what a sample contains.
To create all of them with licences, use "Create demo businesses" on the Licences page instead."""
import argparse
import sys
import uuid

import httpx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--admin-key", required=True)
    ap.add_argument("--kind", default="duka", help="duka, pharmacy or workshop")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, timeout=120)
    email, pw = f"demo-{uuid.uuid4().hex[:6]}@example.com", f"demo-{uuid.uuid4().hex[:10]}"
    r = c.post("/api/system/tenants", headers={"x-admin-key": a.admin_key},
               json={"name": f"Demo {a.kind}", "owner_email": email, "owner_password": pw})
    r.raise_for_status()
    tid = r.json()["id"]
    tok = c.post("/api/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    r = c.post("/api/demo/samples", headers={"Authorization": f"Bearer {tok}"}, json={"tenant_id": tid, "kind": a.kind})
    if r.status_code != 200:
        sys.exit(f"Could not load the sample: {r.json().get('detail', r.text)}")
    print(f"Loaded {r.json()['events']} records and {r.json()['alerts']} alerts.\nLogin: {email}\nPassword: {pw}")


if __name__ == "__main__":
    main()
