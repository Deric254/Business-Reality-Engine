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

## A note on the database connection string
Use the **direct** (non-pooled) connection string from your database host (on Neon, turn "Connection pooling" off before copying it). A pooled connection can keep session locks alive, which is why a sync lock left behind is ignored after 10 minutes, but the direct string avoids the problem.

## 5. Password reset by email (optional)
Without email, "Forgot your password?" cannot send anything. The page says so. People who set security questions (Settings → Security questions) can recover on their own with "Use your security questions instead". Whoever holds the setup key (`ADMIN_API_KEY`) can also generate a one-time reset link on that page.

To send reset emails, add these in Render → Environment: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` (an address your provider has verified) and `APP_BASE_URL` (the address people open, so the link in the email points at it). Brevo and Resend both have free tiers.

Render's free plan blocks outbound ports 25, 465 and 587, so a default SMTP setup times out there. Use a port your provider offers outside that list (Brevo: `2525`; Resend: `2587`), or a paid Render instance.

## Environment variables
| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon connection string (required) |
| `APP_SECRET` | Master secret; JWT, API and connector-encryption keys derive from it |
| `ADMIN_API_KEY` | Setup key for creating the first business; never derived |
| `SMTP_*` / `APP_BASE_URL` | Optional: outgoing email for password resets, invitations and alerts (see section 5) |
| `KEEPALIVE_MINUTES` / `KEEPALIVE_URL` | Self-ping interval / target (defaults to the Render URL) |
| `SERVE_FRONTEND` / `FRONTEND_DIST` | Serve the built web app from the API (on in the Docker image) |
| `WEB_CONCURRENCY` | Workers; keep 1 on the free plan |

## Connectors
Google Sheets (share link), Excel/CSV upload, Web link / REST API, WooCommerce, Shopify, live-feed webhook, database, SFTP, email, shared folder, local agent. The WooCommerce and Shopify presets are unverified against live stores. OAuth connectors (QuickBooks, Zoho, etc.) are not built.
