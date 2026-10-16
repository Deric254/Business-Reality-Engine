# Runbook

## First deploy checklist
1. `cp .env.example .env` and set every secret (compose refuses to start with any missing). Set `CORS_ORIGINS` and `APP_BASE_URL` to the public URL of the web app, `TRUSTED_PROXY_HOPS` to the number of proxies in front of the API.
2. `docker compose up -d --build`. The `migrate` service runs `alembic upgrade head` once, then api and web start. Images have never been built by the authors; if the build fails, fix it before anything else and send the error.
3. Put TLS in front (Caddy, nginx, a cloud load balancer). Nothing in this repo terminates TLS. Tokens travel in headers; do not expose plain HTTP.
4. Create the first business: `curl -X POST $API/api/system/tenants -H "x-admin-key: $ADMIN_API_KEY" -H 'content-type: application/json' -d '{"name":"Acme","owner_email":"you@acme.com","owner_password":"..."}'`. Sign in at the web app; the dashboard links to the 3-step setup.
5. In Settings add an email channel and press **Send test**. If SMTP is unset the test fails with a clear message and forgot-password/invitations are disabled.

## Daily operation
- Health: `GET /api/health` (public), `GET /api/system/scheduler-runs` and `/api/system/connector-health` (admin key) show scheduler runs, failures and stale sources. Detection runs on a schedule on exactly one worker (Redis lock); delivery runs on every worker safely.
- Logs: structured to stdout; `docker compose logs -f api`.
- Audit trail: `/api/audit` (owner) records sign-ins, changes to people, channels, connectors.

## Backups (do this before you need it)
- Database: `BACKUP_DIR=/var/backups/bre DATABASE_URL=... ./scripts/backup.sh` from cron daily (keeps 14). Copy the files off the machine.
- Also back up `CONNECTOR_FILE_ROOT` (shared-folder files) and your `.env`. **Losing `CONNECTOR_ENCRYPTION_KEY` makes stored connector passwords unreadable**; losing `JWT_SECRET_KEY` only signs everyone out.
- Restore into an empty database: `createdb bre_restored && DATABASE_URL=postgresql://.../bre_restored ./scripts/restore.sh backups/bre-....sql.gz && alembic upgrade head`. The script refuses a non-empty target. Rehearse this quarterly; a restore verified at authoring time reproduced identical row counts.

## Upgrades
1. Take a backup. 2. `git pull`, `docker compose up -d --build` (migrations run first). 3. Check `/api/system/scheduler-runs`. Migrations are forward-only; rollback means restoring the backup.

## Troubleshooting
| Symptom | Check |
|---|---|
| Nobody receives alerts | Settings -> channel -> Send test; Deliveries list shows last_error; quiet hours (UTC) and digest settings; severity floor; SMTP/Twilio env |
| "held" deliveries never arrive | Channel is in daily digest mode; digest goes at its UTC hour, once a day |
| Reset emails never arrive | SMTP unset, per-address limit (3/hour), or wrong `APP_BASE_URL` in the link |
| 429 Too many attempts | Auth limiter (10/min/IP) or account lockout (5 wrong passwords -> 15 min). Behind a proxy set `TRUSTED_PROXY_HOPS` or all users share one IP |
| Source shows "Waiting for data" or stale | Connectors page: Sync now shows the error; database/SFTP/IMAP hosts must be public (private hosts are blocked by design) |
| Alerts too noisy or quiet | Accuracy page: log known incidents, run a backtest, adopt the suggested sensitivity; add promotions/holidays under Business |
| Slow dashboard on a large tenant | KPIs are cached 30 s; measured reads stay sub-second to 500k events per tenant; beyond several million consider rollups/partitions |
| Locked-out sole owner, no email | Operator creates a new owner: `POST /api/auth/register` with `x-admin-key` |

## What has not been verified (read this)
Docker image builds, the CI workflow on GitHub, real Slack/Teams/Twilio delivery, LLM narrative quality, MySQL/MSSQL/Oracle/ODBC servers, multi-machine load, screen readers. Each has a code path and tests against local stand-ins; confirm them once in your environment.
