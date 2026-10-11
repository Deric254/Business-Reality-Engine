# Business Reality Engine — Progress Report vs. Blueprint

Status legend: **DONE** = built and tested against real systems. **PARTIAL** = built, with stated gaps. **NOT BUILT** = not started.
Honest scope note: verified in a sandbox with real Postgres, Redis, OpenSSH, Dovecot. NOT verified: Docker builds, visual frontend rendering, real LLM output (no API key), MySQL connector, multi-machine load.

## Part 1 — What exists

- Backend: FastAPI, SQLAlchemy 2.0, Alembic (8 migrations), PostgreSQL 16, Pydantic v2. 15 routers, 37 endpoints.
- Connectors: File (CSV/XLSX), Webhook, Database (Postgres verified; MySQL wired, untested), SFTP, Email (IMAP).
- Ingestion: universal `NormalizedEvent`, idempotent via partial unique index, entity resolution, coverage tracking.
- Intelligence: historical-deviation detection, explanation engine, OLS forecasting, scenario simulation, profit breakdown, Business Memory (cases + outcome measurement), LLM narrative layer (Anthropic / OpenAI / any OpenAI-compatible base URL).
- Ops: APScheduler with run log, Redis-shared rate limit and scheduler lock, admin-only system endpoints.
- Frontend: React + TS + Vite + Tailwind v4: login, KPIs, filters, alert list, alert detail. Verified in headless Chromium (login, refresh, sign-out, dashboard, detail, 375px width); that pass found and fixed two real bugs (filter sent "undefined"; UTC timestamps shown 3h off in Kenya). Not yet checked in other browsers or with a screen reader.

## Part 2 — Section status

| § | Topic | Status | Notes |
|---|---|---|---|
| 1–5 | Vision, principles, architecture | DONE | Reflected in code structure |
| 6 | Relationship traversal | NOT BUILT | Needs `relationships.py` |
| 7 | Entity model | DONE | ResolvedEntity + EntityAlias |
| 8 | Connectors | PARTIAL | File/webhook/DB/SFTP/email done. Missing: local agent, shared folders, UI automation, human-assisted capture, ODBC/JDBC, JSON/XML/TXT; MySQL unverified |
| 9–11 | Normalization, data contract | DONE | |
| 12 | Quality checks | PARTIAL | Validation on ingest; extra checks owed |
| 13 | Entity resolution | DONE (scaled) | Exact-match auto-link, ranked suggestions, human `/merge`. Now indexed + batched: denormalized alias columns, pg_trgm KNN (tenant-scoped GiST), one pass per chunk, unique alias identity so concurrent senders converge on one entity. Old version scanned every entity per new value (400 new customers: 7 events/s; 1,500 did not finish in 5 min). Trade-off: a weak fuzzy pair outside the 40 nearest trigram neighbours with a different surname is no longer suggested |
| 14 | Discovery | NOT BUILT | Discover endpoint |
| 15–18 | Coverage, scheduling | DONE/PARTIAL | Fixed interval for all tenants; per-connector frequency not honored |
| 19–20 | Detection types | PARTIAL | Built and tested: historical deviation with per-metric bad direction (expense rise = problem, sale drop = problem), opportunity (good-direction move ≥ 20%), sustained trend (OLS, R² ≥ 0.7, ≥ 15% total), weekday-aware daily anomaly (median/MAD, tenant timezone), data silence ("unknown": a connector went quiet; self-resolves), fixed-threshold (wired via per-metric absolute limit). Not built: relationship and forecast-deviation detectors, external context |
| 21–24 | Explanation, severity | DONE | |
| 25–26 | Correlation, ranking, suppression, escalation | DONE | Open alerts about the same record type within 72h link to each other (symmetric, capped at 10, cleared when partners close). `priority_rank` = severity base + magnitude + confidence − coverage gap + escalation bonus; list is ordered by it. Cooldown after a human closes an alert (resolved 24h, dismissed 48h, false-positive 7d). Escalation: untouched critical (4h) / high (24h) alerts are stamped and re-notified once as kind `escalation`. Open alerts are updated, never duplicated, and keep their escalation stamp. Thresholds are fixed constants, not yet per-tenant |
| 27–32 | Forecasting, simulation, profit | DONE | Deterministic; assumptions explicit |
| 33–36 | Business Memory | DONE | Cases from alerts, outcome measurement (never claims causation, attribution capped at medium), similar-case retrieval by detector+metric+period (title fallback), `historical_experience` shown on the alert detail page, and outcomes are measured automatically each cycle once the window elapses. Matching is exact-key, not semantic |
| 37–41 | AI reasoning | DONE (untested with real key) | Narrative quality unverified |
| 42 | External context | NOT BUILT | |
| 43–44 | Multi-tenancy, isolation | DONE | Proven with cross-tenant attacks; 404 on mismatch |
| 45 | Question interface | PARTIAL | Deterministic router: profit, sales, expenses, top customers/products/suppliers, next-period forecast, what needs attention, data freshness; period parsing ("last 14 days", "this week"). Every number from SQL/forecasting; unplaceable questions are declined with suggestions, never guessed. Ask box on the dashboard (verified in headless Chromium, desktop and 375px). Not built: LLM intent fallback for unusual phrasing, follow-up questions, what-if questions via chat |
| 46–47 | Performance | PARTIAL | Bulk ingest (INSERT … ON CONFLICT DO NOTHING, chunked): 5,900 events/s with repeating customers (was ~94), about 100-180 events/s when every event brings a brand-new customer (was 7 and collapsing; a further 1.6-3x came from batching the similarity lookups into one LATERAL query per 250 values and moving scoring to C-speed prefilters; the remaining cost is the pg_trgm nearest-neighbour search, ~9 ms/value at 20k names in the tenant and growing with tenant size; runs in this 2-vCPU sandbox vary by ±30%), 1,400-1,500 events/s re-sending duplicates. Single process, one Postgres, in-process (no HTTP). Reads at 500k events for one tenant: detection 0.13 s, KPI breakdown 0.08-0.13 s, forecast 0.15 s, coverage 0.02 s (so daily rollups are not yet needed). KPI results cached 30 s (Redis or in-process, per tenant). **Real HTTP load test** (`scripts/load_test.py`: 4 gunicorn workers, 64 concurrent clients, mixed reads + 50-event batch ingests, 500k-event tenant, 2-vCPU sandbox shared with Postgres, Redis and the client): 122 req/s, 0 errors, ~1,500 events/s ingested while serving reads; read p50 354 ms / p95 1.4 s. That run found and fixed two real bugs: unindexed FK columns made entity deletes take >1 min (stalling the API), and concurrent creation of the same new customer needed serializing (per-tenant advisory lock). Still owed: partitioning, multi-machine test, dedicated hardware numbers |
| 48 | Security | PARTIAL | Mandatory JWT; token revocation (logout, sign-out-everywhere, password change), single-use refresh tokens with reuse detection, account lockout, audit log + viewer, security headers/CSP, bcrypt length guard. Owed: encryption at rest, user management/invitations, dependency scanning (pip-audit), per-role RBAC beyond owner/staff, Redis-less multi-process revocation. See `SECURITY.md` |
| 49 | Alert delivery | PARTIAL | Email (SMTP) + HMAC-signed webhooks (Slack/WhatsApp/SMS can sit behind a webhook relay). Outbox with idempotent enqueue, retries with backoff (1m, 5m, 30m, 2h, 6h) then dead, SKIP LOCKED multi-worker safe, severity floor per channel, SSRF guard, test-send, delivery log. Settings page in the UI. Tested against a real SMTP server and a real webhook receiver. Not built: native SMS/WhatsApp/Slack senders, quiet hours, escalation, per-user preferences |
| 50 | Onboarding/configuration | PARTIAL | Admin tenant creation (+ first owner, default metrics), tenant settings (timezone/currency), per-tenant monitored metrics with thresholds driving the scheduler, Settings UI. Not built: guided first-run wizard, connector setup UI, user invitation/management UI |
| 51 | Self-monitoring | DONE | `scheduler_runs`, `/api/system/*` |
| 52 | Deployment | PARTIAL | Dockerfile (multi-worker server), frontend nginx image (CSP, /api proxy), docker-compose (db, redis, migrate, api, web). Compose file validates; **images never built** (registry unreachable). Verified directly: 3 workers boot, share one Redis rate limit (exactly 300 allowed), scheduler starts per worker. No TLS, backups, or CI yet |
| 53 | Versioning | NOT BUILT | |
| 54 | Testing | PARTIAL | 150 pytest tests: auth, tenant isolation, rate limits, bulk ingestion, notifications, detectors, alert lifecycle, API. Needs coverage for connectors and forecasting |
| 55 | Backtesting | NOT BUILT | |
| 56 | Advisory text in UI | NOT BUILT | |
| 57 | Localization | PARTIAL | Daily-anomaly detector uses the tenant timezone for day boundaries. Rendering/currency formatting not built |

## Part 3 — Constraints hit

- Browser: none via tools, but headless Chromium + Playwright exist in the sandbox; used for the frontend check.
- No LLM key: provider layer built and error-handled, output quality unknown.
- Sandbox resets: Postgres/Redis die between calls and the container can be wiped (happened once; work after the last tarball was lost and redone).
- slowapi incompatible with this FastAPI's lazy router resolution → hand-rolled limiter.
- Docker not available → no image build test.

## Part 4 — Known caveats (do not overlook)

0. Detection was only exercised on synthetic data and tests; thresholds (20% opportunity, R² 0.7, z 3.0, escalation hours) are reasoned defaults, not tuned on real businesses. The correlation rule groups by record type only, so a sales drop and a sales opportunity can link. Escalation is tracked by a stamp in `evidence`.

1. (Resolved) Auth is mandatory per request, with revocation, lockout, audit log and headers. Remaining: encryption at rest, invitations, pip-audit.
2. Idempotency keys on (connector, entity, entity_id); a source that reuses IDs for different records will have later rows treated as duplicates.
3. Ingestion is now bulk (see §46–47). New-entity throughput is bounded by the trigram nearest-neighbour search per new value (batched into one query per 250 values, but still ~9 ms of index work each); a 100k-new-customer historical import takes ~10-15 min. Further levers: lower the KNN limit, or skip similarity for bulk historical loads and run it as a background pass.
4. Detection scans raw `business_events`; at scale needs daily rollups.
5. Redis limiter is fixed-window and fails open.

## Part 5 — How each remaining item gets built

**Security first**
- Mandatory JWT: replace `optional_user_check` with `require_user`; service key limited to `/api/events`, `/api/webhooks`, `/api/system`. Restrict `/api/auth/register` to owners or an admin-key bootstrap. Add a login page; drop `VITE_API_KEY`. Test: every router with no token → 401.
- ~~Token denylist and audit log~~ (done, see §48).
- Dependency scanning (`pip-audit`) in CI; encryption at rest per hosting choice.

**Performance**
- Bulk ingest: `INSERT … ON CONFLICT DO NOTHING RETURNING`, batched commits, per-batch entity-resolution cache.
- Entity resolution: `pg_trgm` GIN index on normalized names + blocking keys; no full scans.
- Daily aggregate table `metric_daily(tenant, entity, field, day, count, sum, distinct_customers)` maintained incrementally; detection reads it.
- KPI endpoint cached in Redis (short TTL, keyed per tenant); partition `business_events` by month.
- Load test with Locust/k6 against multiple workers; report p95 and events/s.

**Detection (§19–20, 25–26)**
- Add detectors as functions returning `Alert` with `evidence`, registered in a detector registry run by the scheduler: trend (OLS slope significance), seasonality (same-weekday baseline), forecast-deviation (actual vs forecast band), threshold (wire + test existing).
- Correlation: group alerts within a time window sharing entity/tenant → `correlated_alert_ids`; `priority_rank` = severity × magnitude × recency; suppression and cooldown per dedup_key; escalation when unacknowledged past a deadline.

**Product (§45, 49, 50, 56, 57)**
- Question interface: intent router (deterministic patterns first, LLM fallback) mapping to existing analytics functions.
- Alert delivery: `notification_channels` per tenant; worker sends email (SMTP) first, then webhook/Slack; retries with backoff; delivery log.
- Onboarding: `POST /api/tenants`, per-tenant monitored-metrics table replacing the hard-coded list in the scheduler.
- Localization: render timestamps/currency from `Tenant.timezone`/currency.

**Connectors (§8)**: JSON/XML/TXT parsers reuse `ColumnMapping`; ODBC/JDBC via a driver registry; the local agent is a small outbound-only service that tails folders and posts to the webhook endpoint with a connector token.

**Deployment (§52)**: Dockerfile (frontend build stage + Python slim), compose with Postgres + Redis, gunicorn with uvicorn workers, `SCHEDULER_ENABLED` on exactly one role or rely on the Redis lock.

## Part 6 — Recommended order

1. ~~Mandatory JWT + register restriction + frontend login~~ (done)
2. ~~Dockerfile/compose~~ (written, unbuilt); extend automated tests to connectors/detection/forecasting
3. ~~Bulk ingest + scalable entity resolution~~ (done); rollups, KPI cache, partitioning, real multi-process load test
4. ~~Detectors, correlation/ranking/escalation~~ (done); relationship and forecast-deviation detectors remain
5. ~~Alert delivery, onboarding~~ (done, see §49/50); question interface
6. Remaining connectors, localization, backtesting
7. Verify frontend in a real browser (highest-value outstanding check)
