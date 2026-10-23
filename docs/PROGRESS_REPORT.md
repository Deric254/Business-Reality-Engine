# Business Reality Engine — Progress Report vs. Blueprint

Status legend: **DONE** = built and tested against real systems. **PARTIAL** = built, with stated gaps. **NOT BUILT** = not started.
Honest scope note: verified in a sandbox with real Postgres, Redis, OpenSSH, Dovecot, local SMTP/HTTP receivers and headless Chromium. NOT verified: Docker image builds, GitHub Actions run, real LLM output (no API key), real Slack/Teams/Twilio, MySQL/MSSQL/Oracle/ODBC servers, multi-machine load, screen readers.

## Part 1 — What exists

- Backend: FastAPI, SQLAlchemy 2.0, Alembic (17 migrations, `alembic check` clean), PostgreSQL 16, Pydantic v2. 22 routers, ~89 endpoints. 357 tests.
- Connectors: file/JSON/XML/TXT, webhook, database (driver registry), SFTP, IMAP email, shared folder, local agent, manual capture, scheduled sync.
- Ingestion: universal `NormalizedEvent`, idempotent bulk path, batched entity resolution, FX conversion, coverage tracking.
- Intelligence: deviation (direction-aware), opportunity, trend, daily anomaly, silence, threshold, forecast-miss, profit-risk and unknown-unknown detectors; correlation, ranking, escalation; business memory; forecasting and simulation; health score; model versioning; backtesting and calibration; LLM narrative layer (Anthropic / OpenAI / any OpenAI-compatible URL).
- Delivery: email, webhook, Slack, Teams, SMS, WhatsApp; quiet hours, digests, filters; password reset and invitations.
- Frontend: React + TS + Vite + Tailwind v4. Pages: alerts, alert detail, Business (health ring, KPIs, data quality, discovery, relationships, context, FX), Data sources (connector setup + manual capture), Accuracy (report, backtest, incidents, model changes), Settings (channels with preferences, people, metrics, password), first-run wizard, forgot/reset/accept-invite, English/Kiswahili. Verified in headless Chromium.
- Ops: scheduler with run log, Redis-shared limits and locks, admin system endpoints, audit log, CI workflow, backup/restore scripts, runbook.

## Part 2 — Section status

| § | Topic | Status | Notes |
|---|---|---|---|
| 1–5 | Vision, principles, architecture | DONE | Reflected in code structure |
| 6 | Relationship traversal | DONE | `relationships.py` + `/api/business/relationships/{id}`: what a customer/product/supplier is tied to, shares, share of business. UI explorer on the Business page |
| 7 | Entity model | DONE | ResolvedEntity + EntityAlias |
| 8 | Connectors | DONE (see caveats) | File (CSV/XLSX/JSON/XML/TXT), webhook, database (driver registry: Postgres + SQLite verified live; MySQL wired, unverified; MSSQL/Oracle/ODBC need server drivers and are unverified), SFTP, email (IMAP), shared folder (tenant-jailed, content-hash dedupe), local agent (`agent/`, outbound-only), manual capture (idempotent), scheduled pull sync with advisory locks. Setup UI built. Not built: UI-automation/RPA, JDBC |
| 9–11 | Normalization, data contract | DONE | |
| 12 | Quality checks | DONE | Validation on ingest plus `/api/business/quality` (failed %, missing fields, per-source) and a quality-driven alert kind; shown on the Business page |
| 13 | Entity resolution | DONE (scaled) | Exact-match auto-link, ranked suggestions, human `/merge`. Now indexed + batched: denormalized alias columns, pg_trgm KNN (tenant-scoped GiST), one pass per chunk, unique alias identity so concurrent senders converge on one entity. Old version scanned every entity per new value (400 new customers: 7 events/s; 1,500 did not finish in 5 min). Trade-off: a weak fuzzy pair outside the 40 nearest trigram neighbours with a different surname is no longer suggested |
| 14 | Discovery | DONE | `/api/business/discovery`: record types, completeness, history, suggested metrics (one click to watch) |
| 15–18 | Coverage, scheduling | DONE/PARTIAL | Fixed interval for all tenants; per-connector frequency not honored |
| 19–20 | Detection types | PARTIAL | Built and tested: historical deviation with per-metric bad direction (expense rise = problem, sale drop = problem), opportunity (good-direction move ≥ 20%), sustained trend (OLS, R² ≥ 0.7, ≥ 15% total), weekday-aware daily anomaly (median/MAD, tenant timezone), data silence ("unknown": a connector went quiet; self-resolves), fixed-threshold (wired via per-metric absolute limit). Forecast-deviation is built (it stays quiet while the main comparison already reports the same drop). Not built: external context |
| 21–24 | Explanation, severity | DONE | |
| 25–26 | Correlation, ranking, suppression, escalation | DONE | Open alerts about the same record type within 72h link to each other (symmetric, capped at 10, cleared when partners close). `priority_rank` = severity base + magnitude + confidence − coverage gap + escalation bonus; list is ordered by it. Cooldown after a human closes an alert (resolved 24h, dismissed 48h, false-positive 7d). Escalation: untouched critical (4h) / high (24h) alerts are stamped and re-notified once as kind `escalation`. Open alerts are updated, never duplicated, and keep their escalation stamp. Thresholds are fixed constants, not yet per-tenant |
| 27–32 | Forecasting, simulation, profit | DONE | Deterministic; assumptions explicit |
| 33–36 | Business Memory | DONE | Cases from alerts, outcome measurement (never claims causation, attribution capped at medium), similar-case retrieval by detector+metric+period (title fallback), `historical_experience` shown on the alert detail page, and outcomes are measured automatically each cycle once the window elapses. Matching is exact-key, not semantic |
| 37–41 | AI reasoning | DONE (untested with real key) | Narrative quality unverified |
| 42 | External context | DONE | Holidays (synced), promotions, events; alerts during them are judged with context. UI on the Business page. Holiday data source is a built-in calendar, not a live feed |
| 43–44 | Multi-tenancy, isolation | DONE | Proven with cross-tenant attacks; 404 on mismatch |
| 45 | Question interface | DONE (router) | Deterministic router with LLM intent fallback that only classifies, never answers. LLM path untested with a real key |
| 46–47 | Performance | PARTIAL | Bulk ingest (INSERT … ON CONFLICT DO NOTHING, chunked): 5,900 events/s with repeating customers (was ~94), about 100-180 events/s when every event brings a brand-new customer (was 7 and collapsing; a further 1.6-3x came from batching the similarity lookups into one LATERAL query per 250 values and moving scoring to C-speed prefilters; the remaining cost is the pg_trgm nearest-neighbour search, ~9 ms/value at 20k names in the tenant and growing with tenant size; runs in this 2-vCPU sandbox vary by ±30%), 1,400-1,500 events/s re-sending duplicates. Single process, one Postgres, in-process (no HTTP). Reads at 500k events for one tenant: detection 0.13 s, KPI breakdown 0.08-0.13 s, forecast 0.15 s, coverage 0.02 s (so daily rollups are not yet needed). KPI results cached 30 s (Redis or in-process, per tenant). **Real HTTP load test** (`scripts/load_test.py`: 4 gunicorn workers, 64 concurrent clients, mixed reads + 50-event batch ingests, 500k-event tenant, 2-vCPU sandbox shared with Postgres, Redis and the client): 122 req/s, 0 errors, ~1,500 events/s ingested while serving reads; read p50 354 ms / p95 1.4 s. That run found and fixed two real bugs: unindexed FK columns made entity deletes take >1 min (stalling the API), and concurrent creation of the same new customer needed serializing (per-tenant advisory lock). Still owed: partitioning, multi-machine test, dedicated hardware numbers |
| 48 | Security | PARTIAL | Mandatory JWT; token revocation (logout, sign-out-everywhere, password change), single-use refresh tokens with reuse detection, account lockout, audit log + viewer, security headers/CSP, bcrypt length guard. Owed: encryption at rest, email invitations and self-service password reset, dependency scanning (pip-audit), per-role RBAC beyond owner/staff, Redis-less multi-process revocation. See `SECURITY.md` |
| 49 | Alert delivery | DONE (see caveats) | Email, signed webhooks, Slack, Teams, SMS and WhatsApp (Twilio). Outbox with idempotent enqueue, leased claiming (no double sends across workers), retries with backoff, dead state. Per-channel quiet hours (critical bypasses), daily digest for low-urgency alerts, kind/entity filters. Verified against real local SMTP/HTTP receivers; **not verified against real Slack, Teams or Twilio** (Twilio endpoint is pointed at a local fake in tests) |
| 50 | Onboarding/configuration | DONE | Admin tenant creation, tenant settings, monitored metrics, user management with email invitations (single-use hashed tokens, 7 days) and self-service forgot/reset password (60 min, hashed, single-use, no enumeration, throttled, sessions revoked), temporary-password fallback when SMTP is absent, first-run wizard, connector setup UI |
| 51 | Self-monitoring | DONE | `scheduler_runs`, `/api/system/*` |
| 52 | Deployment | PARTIAL | Dockerfile (multi-worker), frontend nginx image (CSP, /api proxy), docker-compose (db, redis, migrate, api, web; SMTP/Twilio env passed through), GitHub Actions CI (tests, `alembic check`, pip-audit, tsc/build, npm audit, image builds), backup/restore scripts (restore verified against a real dump), runbook. **Docker images were never built here** (registry unreachable) and the CI workflow has not run on GitHub yet. No TLS termination bundled: put a reverse proxy/load balancer in front |
| 53 | Versioning | DONE | Business-model versions (`model_changes`), auto-detected location/product changes, declared changes with re-baselining windows, version stamped on alerts. UI on the Accuracy page |
| 54 | Testing | DONE (backend) | 357 pytest tests across auth, isolation, rate limits, bulk ingestion, delivery, detectors, lifecycle, memory, questions, connectors, model versions, backtesting, password recovery. Frontend verified by scripted headless-Chromium flows, not by a committed UI test suite |
| 55 | Backtesting | DONE | `/api/accuracy/backtest` replays detectors over history at several sensitivities, precision/recall against logged incidents, suggested threshold; live accuracy report with calibration. Limits are stated in the output (silence and case-outcome checks are not replayed) |
| 56 | Advisory text in UI | DONE | Plain-language headlines, hints and method notes on each screen; accuracy notes refuse to show rates below a minimum sample |
| 57 | Localization | DONE (basic) | Tenant timezone and base currency, FX rates with date-effective conversion and reprocessing, UI in English and Kiswahili (navigation and main labels; deep screens fall back to English). Number formatting is fixed en-US grouping |

## Part 3 — Constraints hit

- No LLM key: provider layer built and error-handled, output quality unknown.
- Sandbox resets: processes die between calls and the container was wiped once mid-project (work after the last delivered zip was redone from that zip). Delivering the zip after each block is what makes this recoverable.
- Docker not available → no image build test. No outbound access to Slack/Teams/Twilio → delivery verified against local fakes.
- slowapi incompatible with this FastAPI's lazy router resolution → hand-rolled limiter.

## Part 4 — Known caveats (do not overlook)

0. Detection was only exercised on synthetic data and tests; thresholds (20% opportunity, R² 0.7, z 3.0, escalation hours) are reasoned defaults, not tuned on real businesses. The correlation rule groups by record type only, so a sales drop and a sales opportunity can link. Escalation is tracked by a stamp in `evidence`.

1. (Resolved) Auth is mandatory per request, with revocation, lockout, audit log, headers, invitations and password recovery. Remaining: encryption at rest (use disk/DB encryption from your host), pip-audit results (runs in CI, not yet run on GitHub).
2. Idempotency keys on (connector, entity, entity_id); a source that reuses IDs for different records will have later rows treated as duplicates.
3. Ingestion is now bulk (see §46–47). New-entity throughput is bounded by the trigram nearest-neighbour search per new value (batched into one query per 250 values, but still ~9 ms of index work each); a 100k-new-customer historical import takes ~10-15 min. Further levers: lower the KNN limit, or skip similarity for bulk historical loads and run it as a background pass.
4. Detection scans raw `business_events`; at scale needs daily rollups.
5. Redis limiter is fixed-window and fails open.

## Part 5 — What is left, and how

- **Run the unverified things once you have the access**: build the Docker images (`docker compose up -d --build`), push to GitHub so CI runs, send a real alert to your Slack/Teams/WhatsApp and a real SMS, point `ANTHROPIC_API_KEY` (or another provider) at the reasoning layer and read the narratives critically, connect a real MySQL/MSSQL source. Each is a short, mechanical check; the code paths exist.
- **Tune on real data**: detector thresholds are reasoned defaults. Use the Accuracy page: log known incidents, run a backtest, adopt the suggested sensitivity.
- **Scale beyond one database**: daily rollup table, monthly partitions of events, read replica. Not needed at the measured sizes (500k events/tenant, sub-second reads); revisit when a tenant exceeds several million events.
- **UI polish**: deeper Kiswahili coverage, screen-reader and cross-browser pass, committed Playwright suite.
- **Product extensions not in the blueprint scope built so far**: UI automation/RPA connectors, JDBC, multi-business membership for one user.

## Part 6 — Audit, October 2026

What was found and changed, so nobody has to rediscover it:

- **The scheduled detection job was added paused** (`next_run_time=None` creates a paused APScheduler job), so checks only ran right after new data arrived and a source that went quiet was never noticed. Fixed; `test_scheduled_jobs_are_not_paused` guards it.
- **Alert wording**: every title names what it is compared with ("Sales are down 59% from your usual week"), every detail gives the figures, the gap and the currency, and changes past +100% read as multiples ("15 times"). One text builder (`comparison_text`) feeds every screen so they cannot disagree.
- **One story, one alert**: the forecast-miss alert is suppressed while the main comparison alert is open or recently closed by a person.
- **Alerts end when the problem does**: an untouched alert closes itself once the problem is well inside its threshold; alerts a person is working on stay open; self-closed alerts do not trigger the quiet period.
- **One-off detection**: when one record (4x the average of the others, at least half the change) explains a rise, a drop or a loss, the alert names it and the next step says what to do if it was planned.
- **API**: `GET /api/alerts/?state=open|closed` (and any single state), `limit` (default 300, max 1000); the detail response includes `related` (id and title of related alerts). The dashboard asks for open alerts only.
- **Uploads** are read with a hard cap (limit + 1 byte), so an oversized file is refused without being held in memory, whatever sits in front of the API.
- **Housekeeping**: a daily job removes expired revoked-login records.
- **Owners' screens**: period buttons (Last 7 days / 30 / 90 / 180), currency on amounts, quiet hours and digest hour shown in the business's own time (stored in UTC), plain names for alert types, a prompt when no one is set up to be notified.
- Removed: unused sample-data module (`scripts/seed_demo.py` does the job), two unused components, unused imports.
- Checked: every route that takes a business id refuses another business's login (68 routes probed); `pip-audit` and `npm audit` report no known vulnerabilities.
- Still open: alert text is English only; the Docker images were not built in the audit environment.
