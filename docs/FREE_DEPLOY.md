# Free deployment (no monthly cost, room to scale)

```
Browser ──► Cloudflare Pages (web page, free, never sleeps)
   │
   └─ HTTPS ─► Free VM (Google e2-micro or Oracle): Caddy ─► API + scheduler (always on)
                                                        │
                                                        └─► Neon Postgres (free, no expiry)
```

Everything here is on permanent free tiers. Free terms change, so recheck each sign-up page. Not verified in the authoring sandbox: the Docker builds, Caddy certificate issuance, and the Neon / Cloudflare / VM steps (they need your accounts). The app itself (357 tests) and the frontend build were run.

## 1. Neon database (5 min)
1. neon.tech → sign up → create a project (region closest to your VM).
2. Dashboard → **Connect** → turn **Connection pooling OFF** → copy the string (ends `?sslmode=require`). This is `DATABASE_URL`.

## 2. Free VM (10 min)
**Google Cloud** (recommended): console.cloud.google.com → create project → *Compute Engine* → *Create instance*:
- Machine type **e2-micro**, region **us-central1 / us-west1 / us-east1** (only these are free)
- Boot disk: Ubuntu 24.04, **Standard persistent disk**, 30 GB
- Firewall: tick **Allow HTTP** and **Allow HTTPS**
- Then *Billing → Budgets & alerts*: set a **$1 budget alert**.
Reserve the IP: *VPC network → IP addresses* → promote the external IP to **static** (free while attached to a running VM).

**Oracle** (alternative): Always Free VM, open ports 80/443 in the VCN security list **and** the instance firewall.

## 3. Name for the API
No domain needed: take the VM's external IP, replace dots with dashes, add `.sslip.io`.
`34.12.56.78` → `34-12-56-78.sslip.io`. (If you own a domain, use an A record instead.)

## 4. Install Docker on the VM
SSH in (the console has an **SSH** button), then:
```bash
git clone https://github.com/<you>/<repo>.git app && cd app
bash deploy/free/setup-server.sh
```
Log out and back in.

## 5. Configure and start
```bash
cd app/deploy/free
cp env.example .env && nano .env      # fill every value; generate secrets as the comments show
docker compose up -d --build          # first build takes several minutes
docker compose ps
curl https://34-12-56-78.sslip.io/api/health/
```
Create your first business (replace host and key):
```bash
curl -X POST https://HOST/api/system/tenants -H "x-admin-key: <ADMIN_API_KEY>" -H "Content-Type: application/json" -d '{"name":"My Business", "owner_email":"you@example.com", "owner_password":"<strong password>"}'
```
Then open your Pages URL and log in with that email and password.

## 6. Web page on Cloudflare Pages
dash.cloudflare.com → *Workers & Pages* → *Create* → *Pages* → connect your GitHub repo:
- Root directory: `frontend`
- Build command: `npm run build:pages`
- Output directory: `dist`
- Environment variable: `VITE_API_BASE` = `https://34-12-56-78.sslip.io`
Deploy. Put the resulting `https://<project>.pages.dev` into `CORS_ORIGINS` and `APP_BASE_URL` in the server `.env`, then `docker compose up -d`.

## 7. Updating
On the VM: `bash deploy/free/update.sh`. Web page redeploys itself on every push to GitHub.

## 8. Backups
Neon keeps point-in-time history on the free plan (short window). Also run weekly from anywhere with `pg_dump`: `DATABASE_URL=... bash scripts/backup.sh`.

## Limits to know
- 1 GB VM, 1 API worker: fine for a few small businesses. Neon free = 500 MB.
- Free VM is single machine: no failover. A reboot restarts everything (`restart: unless-stopped`).
- Neon sleeps its compute after ~5 idle minutes; the first query after a wake is slower. Detection runs every 15 min, which is fine.
- Without Redis, rate limits and revoked-session state live in the single worker's memory and reset on restart (see SECURITY.md).

## Scaling later (no code change)
Bigger VM → raise `WEB_CONCURRENCY`, add Redis (`REDIS_URL`) → move to a paid Neon plan → or use the full `docker-compose.yml` at the repo root.
