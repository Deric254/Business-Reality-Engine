# Deploy on Render (one service, one URL)

```
Browser ──► Render web service (web app + API + scheduler, one Docker image) ──► Neon Postgres (free)
                      ▲
                      └─ UptimeRobot / cron-job.org pings /healthz every 5 min
```

The web app is built into the same image as the API, so there is no second host and no CORS setup.

Not verified in the authoring sandbox: the Docker build and the Render / Neon / UptimeRobot steps (they need your accounts).

## 1. Neon database
neon.tech → create project → **Connect** → pooling **OFF** → copy the string. That is `DATABASE_URL` (`postgres://` or `postgresql://` both work).

## 2. Render
render.com → **New → Blueprint** → pick the repo. `render.yaml` sets everything up:
- Docker web service, health check `/healthz`
- `ADMIN_API_KEY` and `APP_SECRET` are generated for you (other secrets are derived from `APP_SECRET`)
- 1 worker and a small DB pool, so it fits in free-tier memory

Render will ask for one value: **DATABASE_URL** (paste the Neon string). Deploy.

## 3. First sign-in
Open `https://<service>.onrender.com`. On first run you'll see **Create your business**. The setup key is the `ADMIN_API_KEY` from Render → Environment. After that, log in normally.

## 4. Keep it from sleeping (important)
Render's free plan puts a service to sleep after ~15 minutes without *outside* traffic. The built-in self-ping (`KEEPALIVE_MINUTES`, default 10) helps while it's awake but can't wake a sleeping service. So add a free external pinger:
- UptimeRobot or cron-job.org → HTTP monitor on `https://<service>.onrender.com/healthz` every 5 minutes.

Honest limits: free instances can still restart or cold-start (~30–60s), and Neon free has monthly compute-hour limits. The Render **Starter** plan (~$7/mo) removes sleeping entirely.

## Environment variables
| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon connection string (required) |
| `APP_SECRET` | Master secret; JWT, API and connector-encryption keys derive from it |
| `ADMIN_API_KEY` | Setup key for creating the first business; never derived |
| `KEEPALIVE_MINUTES` / `KEEPALIVE_URL` | Self-ping interval / target (defaults to the Render URL) |
| `SERVE_FRONTEND` / `FRONTEND_DIST` | Serve the built web app from the API (on in the Docker image) |
| `WEB_CONCURRENCY` | Workers; keep 1 on the free plan |

## Connectors
Google Sheets (share link), Excel/CSV upload, Web link / REST API, WooCommerce, Shopify, live-feed webhook, database, SFTP, email, shared folder, local agent. The WooCommerce and Shopify presets are unverified against live stores. OAuth connectors (QuickBooks, Zoho, etc.) are not built.
