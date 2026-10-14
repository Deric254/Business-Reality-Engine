# Free deployment (no card, no monthly cost)

```
Browser ──► Cloudflare Pages (web page, free)
   │
   └─ HTTPS ─► Render free web service (API + scheduler) ─► Neon Postgres (free)
                        ▲
                        └─ UptimeRobot ping every 5 min (keeps it awake)
```

None of these sign-ups should ask for a card. Free terms change: if any page asks for card details, stop and recheck its current terms. Not verified in the authoring sandbox: the Docker build and the Render / Neon / Cloudflare / UptimeRobot steps (they need your accounts).

## 1. Neon database
neon.tech → create project → **Connect** → Connection pooling **OFF** → copy the string (ends `?sslmode=require`). This is `DATABASE_URL`.

## 2. API on Render
render.com → sign in with GitHub → **New → Web Service** → pick the repo.
- Runtime: **Docker**, root directory blank, instance type **Free**
- Docker Command: **leave blank** (the Dockerfile runs migrations when `RUN_MIGRATIONS=1`)
- Health Check Path: `/api/health/`
- Environment: every variable in `deploy/render/env.example` (leave `APP_BASE_URL` / `CORS_ORIGINS` as placeholders for now)

Deploy. Your API URL is `https://<service>.onrender.com`. Check `https://<service>.onrender.com/api/health/`.

## 3. First business
```bash
curl -X POST https://<service>.onrender.com/api/system/tenants -H "x-admin-key: <ADMIN_API_KEY>" -H "Content-Type: application/json" -d '{"name":"My Business","owner_email":"you@example.com","owner_password":"<strong password>"}'
```

## 4. Web page on Cloudflare Pages
dash.cloudflare.com → *Workers & Pages* → *Create* → *Pages* → connect the repo:
- Root directory: `frontend`
- Build command: `npm run build:pages`
- Output directory: `dist`
- Environment variable: `VITE_API_BASE` = `https://<service>.onrender.com`

Then set `APP_BASE_URL` and `CORS_ORIGINS` on Render to the `https://<project>.pages.dev` URL and let it redeploy.

## 5. Keep the API awake
Render free services sleep after 15 minutes idle, and detection only runs while awake. uptimerobot.com (free) → HTTP monitor on `https://<service>.onrender.com/api/health/`, every 5 minutes. One always-awake service uses about 720 of the 750 free hours per month; do not run a second free web service in the same workspace.

## 6. Updating
Push to GitHub: Render and Cloudflare Pages both redeploy automatically.

## 7. Backups
Neon keeps a short point-in-time history on the free plan. Also run weekly from anywhere with `pg_dump`: `DATABASE_URL=... bash scripts/backup.sh`.

## Limits to know
- Render free: 512 MB RAM, 0.5 vCPU, may restart at any time, ~1 min cold start if it ever sleeps.
- Neon free: 500 MB, compute sleeps after ~5 idle minutes (first query after a wake is slower).
- No Redis: rate limits and revoked sessions live in the single worker's memory and reset on restart (see SECURITY.md).
- `TRUSTED_PROXY_HOPS=1` is a starting value; verify rate limiting sees real client IPs (see SECURITY.md).

## Scaling later
Paid Render instance (no sleep) → add Redis (`REDIS_URL`) → paid Neon plan → or use the full `docker-compose.yml` at the repo root.
