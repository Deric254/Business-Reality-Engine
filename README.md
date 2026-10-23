---
title: Business Reality Engine
emoji: 📈
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
---

# Business Reality Engine

Always-on business intelligence and decision layer. It connects to the systems a business already uses, builds a living model of the business, watches it continuously, detects what matters, explains why, alerts the right people, and remembers decisions and outcomes. Every business (tenant) is isolated from every other.

- Product blueprint: `docs/blueprint.md`
- What is built vs. what is left, section by section: `docs/PROGRESS_REPORT.md`
- Security design, residual risks and audit history: `SECURITY.md`
- Operating it (deploy, backup, restore, upgrades, troubleshooting): `docs/RUNBOOK.md`
- Local agent for on-premise systems: `agent/README.md`

## Quick start (Docker)

```bash
cp .env.example .env          # fill POSTGRES_PASSWORD, API_KEY, ADMIN_API_KEY, JWT_SECRET_KEY, CONNECTOR_ENCRYPTION_KEY
docker compose up -d --build  # db, redis, migrations, api, web  ->  http://localhost:8080
```

Generate secrets with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`; the encryption key must be a Fernet key (`python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
Compose has not been built in the authoring environment; see the runbook's first-deploy checklist.

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt pytest httpx aiosmtpd
cp .env.example .env                         # DATABASE_URL, API_KEY, ADMIN_API_KEY, JWT_SECRET_KEY, CONNECTOR_ENCRYPTION_KEY
createdb business_reality && alembic upgrade head
uvicorn app.main:app --reload                # API on :8000

cd frontend && npm install && npm run dev    # UI on :5173 (set VITE_API_BASE if the API is elsewhere)
python -m pytest tests -q -W ignore          # 357 tests; needs Postgres at DATABASE_URL
```

## Licences and the master account
Every business comes in through a licence key: one key creates one business, once, and can be revoked at any time.
- **Create the master account (once):** sign in as yourself, then `POST /api/master/bootstrap` with header `x-admin-key: <ADMIN_API_KEY>` and body `{"email": "<your email>"}`. A **Licences** link then appears in the sidebar for you and nobody else (everyone else gets a 404 on `/api/master/*`).
- **Issue keys** on the Licences page (price per month, who it is for). A key is shown once and stored only as a hash. The customer opens `/activate`, enters the key, their business name, email and password, and is signed in. Two people submitting the same key at once create one business, never two.
- **Revoke** from the same page: the business is refused on its next request (within about 20 seconds across several servers without Redis; at once with it) and cannot sign in. Your own master licence cannot be revoked.
- **Money:** the page shows only what you have recorded as received (**Record payment**, with an optional reference such as the M-Pesa code; each payment extends *paid through* by a month). Monthly recurring, collected this month and all time are sums of that ledger. Charging the $1 automatically needs a payment provider account (Paystack, Stripe or similar); its webhook would call the same `licensing.record_payment`. Until then, payment is recorded by hand.
- **Switch it on:** set `LICENCE_ENFORCED=true` and redeploy. The migration gives every business that exists today a free active licence, so nobody is locked out when you do. With the switch off (the default) licences are issued and tracked but not required.

## Periods: one definition of "last N days"
`app/core/periods.py` is the only place a period is defined: N whole days on the business's own calendar, ending with today, compared with the N days before it; **All time** runs from the first sale or expense to the end of today with nothing to compare. The Overview, the chart, the health strip, Ask and Check my data all take their boundaries from it, and the chart groups by the business's day, never the UTC day. Alerts compare complete days up to the end of yesterday and now say which days: that is why an alert's "7 days" can differ from the Overview's by today's sales.

## Scale
Measured on one business with 860,000 records from three sources (a POS, an M-Pesa statement, accounting), PostgreSQL on a small machine: dashboard numbers 0.3 to 0.5 s, a full detection cycle 3.9 s, a question planned by the AI 1.0 s, a 5,000-row upload into that business 3.6 s (a single row 0.1 s), disconnecting a 400,000-record source 3.5 s. Things that are one-off and slow: confirming a merge that pairs 320,000 records takes about 30 s and undoing it about 15 s. Design rules that keep it this way: reconciliation runs as set-based SQL inside the database (never rows pulled into Python) and incrementally, only against records near new arrivals; it uses grouping instead of joins so a stale query plan after a bulk import cannot turn it quadratic; per-business lookups (location spellings, branch and product names) live in small tables or short caches, so no request scans the whole history to find them. Not measured: much beyond a million records in one business, or many hundreds of businesses in one database; at that size the events table would need partitioning.

## Built for volume and many sources
Measured on one business with about 860,000 records across three sources (a POS, an M-Pesa statement, an accounting export) on a single small Postgres:

| Operation | Time |
|---|---|
| Dashboard numbers (30 and 365 days) | about 1 s |
| Business health, source contributions | 0.4 to 0.5 s |
| Plain question / weekday / forecast | 0.1 s |
| AI-planned question (grouping, comparison over a year) | about 1.2 s |
| Full detection cycle | about 3 s |
| Upload of 5,000 rows | about 3 s (5 s while a merge rule is active); one row: 0.03 s |
| Suggest merges between sources (cached 5 min) | about 4 s |
| Confirm a merge that pairs 320,000 records (one time) | about 30 s |
| Undo that merge / disconnect a 400,000-record source | about 17 s / 3 s |

How it stays that way: aggregation and matching happen in the database, never by loading records into Python; after a merge, new records are paired only against records near them in time; spellings of places live in a small lookup table; branch and product names for the AI planner are cached. Matching works from small temporary tables with fresh statistics, because the planner cannot estimate the big table right after a bulk import.

Not measured: more than about 1 million records in one business, or more than about 8 sources compared pairwise (suggestions stop there). The events table is not partitioned; at tens of millions of rows per business, partition it by tenant and month.

## Ask anything, with numbers you can trust
- **Common questions need no AI** (profit, sales, expenses, top customers, branches, best weekday, forecast, what needs attention).
- **Everything else is planned by the AI and answered by SQL.** With an AI provider connected, a question with a filter, a grouping or a comparison ("best products at Maua last month", "sales by week against last year", "average sale this week") is turned into a query plan: a small JSON object where every field is one of a fixed set of choices. The plan is validated, branch and product names must exist in the business's records, and then it runs as ordinary SQL. The AI never sees or writes a number; the answer states exactly what was computed (period, filters, comparison), so a misread question is visible. Customer names are never sent to the AI.
- **Days start at the business's local midnight** (tenant timezone), for "today", weekday and daily groupings.
- **Forecasts follow the weekly rhythm.** The next 7 or 30 days are projected day by day from the business's own pattern (a Saturday is not a Tuesday; a closed Sunday stays at zero) plus a trend, with a likely range from how far past days missed. It needs four weeks of history; with less, the older straight-line projection is used. Holidays and paydays are not modelled in the forecast.

## When something does not show, the app says why
`GET /api/business/data-check` (the **Check my data** panel on the Overview and Data pages) reports, per source: whether it ever synced, why the last sync failed (stored on the source), how many records were counted, left out and why, the date range of its records against the period shown (and offers to widen it), records with no amount, whether the AI assistant is connected, and whether the database has been migrated to this version (`/api/health/` also reports it). The Overview no longer says "nothing needs your attention" when sources are connected but nothing falls in the period shown; it says what it found.
Without an AI assistant connected, Ask answers a fixed list of questions and says so, with a link to connect one.

## A tracked sheet is a mirror, not a stream
A Google Sheet or file link is re-read on every sync and the stored records are brought in line with it (`app/core/snapshot.py`):
- a row you add appears; a figure you edit is **replaced** (it is never counted twice); a row you delete is **removed**. Each record carries a fingerprint of what the source gave, so an edit is recognised even when the receipt number stays the same.
- Only a source that returns its whole content (a sheet or file link) removes records. A JSON API, a database query, and files arriving by SFTP, email or folder add and update but never remove, because they may show only the latest records.
- **Safeguard:** if a sync would change more than half of a sizeable source (the link now points at another tab, someone emptied the sheet) nothing is touched; the owner is asked to confirm. Uploads add to what is there; "Replace with a file" (owner) makes the file the whole truth.
- A record left out of the totals (a date in the future, a negative amount) is listed under its source with the reason. Fix it in the source and sync: the corrected record replaces it.
- A time with no timezone (a sheet, a typed entry) is read on the **business's own clock**, so a sale from this afternoon is never "in the future" and days start at local midnight.
- A source that was checked and had nothing new is still marked as checked; a sync lock left over by a pooled database connection cannot block a source for more than 10 minutes.

## Alerts follow the data
- Detection runs a few minutes after new records arrive (not only on a timer), at most once per business per two minutes. Set `DETECT_AFTER_INGEST=false` to turn this off.
- Live detection compares **whole days**: the day in progress is incomplete and would make every week look like a drop.
- **Spending exceeds sales** is raised from the first days of data, from plain arithmetic on the last 30 days (high when spending is at least 1.25 times sales). It stays quiet if no sales are recorded at all, which usually means the sales source is not connected.

## Built for large histories and many sources
Measured on one business with 860,000 records across three sources (a POS, an M-Pesa statement, accounting), on a small shared database:
- Dashboard numbers 0.4 to 0.8 s, health score 1.1 s, a detection cycle 7 to 8 s, questions 0.1 to 2 s, disconnecting a 400,000-record source 7 s.
- Ingesting 5,000 rows takes 4 to 6 s (about 1,000 rows a second, mostly entity resolution and index writes); one row takes under 0.1 s, even with a merge rule active.
- Reconciliation runs entirely in the database (no rows are loaded into Python) and has no row limit. It pairs about 320,000 records in roughly 50 s and undoes that in about 30 s; a typical business (tens of thousands) takes seconds. After a merge, new records are paired incrementally, only against records near them in time.
- Suggestions and the "only in one source" lists look at the last 60 days, are tried inside a transaction that is rolled back, and are cached for 5 minutes.
- Place spellings live in a small per-business lookup table, so ingestion never scans the events table to normalise them; the lists of branch and product names the AI may filter by are cached.
Not measured: more than about a million records in one business, or many dozens of sources. Beyond that the events table would want partitioning by tenant and time.

## Reads data as the business keeps it
Nobody has to reshape their files. On connect, the columns are detected (by an AI when a provider key is set, by rules otherwise; every AI answer is checked against the real columns) and the person only confirms.
- **Report titles above the header** (business name, date range) are skipped; error messages still quote the row number as the sheet shows it.
- **Money in / money out in separate columns** (M-Pesa and bank statements, debit/credit ledgers): money in is a sale, money out is an expense. These are interpretations, so the wizard says so and the person can change them.
- **No total column**: the amount is quantity × unit price. A missing price leaves the amount empty; it is never invented.
- **A Type column** (Sale, Purchase, Withdrawal...) decides sale or expense per row.
- Dates day-first, amounts such as `KES 1,500` or `1,500/=`, blank and TOTAL rows: see below.
Not handled yet: dates or months spread across columns, and one workbook with several tabs as one source (connect each tab separately).

## One shared dataset
Every source (file, sheet, link, database, webhook, typed entries) is normalised into the same event table, so every total, alert and chart reads one place.
- **No double counting across sources.** If a second source brings a record with the same id (a receipt or order number), the same amount and the same day as one already counted, it is kept for provenance, marked `duplicate`, and left out of every total. Records without a shared id are never merged, because two real sales cannot be told apart safely.
- **Same sales in two sources with no shared id** (a POS and an M-Pesa statement): the engine pairs records one-to-one by amount and moment (within 15 minutes, or the same day for sources that hold dates only) and, when enough match, suggests counting them once. The owner confirms (`POST /api/connectors/{other}/merge-into/{keep}`); the richer source (more branch/product/customer detail) is suggested as the main record. After a merge, records still arriving are paired automatically in either load order, records with no partner stay counted and are listed per source ("only in M-Pesa: 3 payments, 2,664") so unrecorded sales or missing payments are visible, and `DELETE /api/connectors/{other}/merge` undoes it exactly. Nothing is ever merged without that confirmation.
- **One spelling per place.** "nairobi", "Nairobi " and "NAIROBI" are one location; the spelling already in the business wins.
- **Messy sheets are read sensibly.** `03/09/2026` is 3 September unless the column proves month-first; amounts like `KES 1,500`, `1,500/=` or `(1,500)` are numbers and a dash means "nothing"; blank rows are ignored. A stray row (a TOTAL line, a note) is skipped and named in the import summary instead of rejecting the whole sheet, but if most rows are unreadable the import fails, because the column mapping is probably wrong.
- **One currency.** Amounts are converted to the business's base currency on the way in.
- **Disconnect removes a source and its data** (`DELETE /api/connectors/{id}`, owner only, audit-logged). A record that another source also held stays counted. Use Pause to stop a source without deleting anything.
- The Data page shows, per source, how many records are counted, how many were already counted from another source, and how many were rejected (`GET /api/connectors/contributions`).

## Who calls what

| Caller | Credential | Reaches |
|---|---|---|
| People in the web app | per-user JWT (owner or staff) | everything in their own business; owners change settings |
| Connected systems | per-connector webhook token; service key `x-api-key` for event ingestion only | `/api/webhooks/...`, `/api/events/...` |
| The operator | `x-admin-key` | `/api/system/*`, creating businesses |

Create the first business and its owner: `POST /api/system/tenants` with `x-admin-key` (name, owner_email, owner_password).

## Optional integrations

- **Email** (alerts, password reset, invitations): `SMTP_*` and `APP_BASE_URL`.
- **SMS / WhatsApp**: Twilio (`TWILIO_*`). **Slack / Teams**: paste an incoming-webhook URL per channel in Settings.
- **LLM narratives and question fallback**: `LLM_PROVIDER` = `anthropic` | `openai` | `openai_compatible` (Groq, Together, Ollama...). Without it everything works; the natural-language layer is simply skipped.

## Layout

```
app/api/        routers          app/core/       detection, ingestion, delivery, auth, forecasting, memory...
app/connectors/ source adapters  app/reasoning/  provider-agnostic LLM layer
agent/          on-premise local agent           alembic/        migrations
frontend/       React + TypeScript + Tailwind    scripts/        seed, load test, backup, restore
tests/          357 tests                        .github/        CI
```

## Free hosting
See [docs/FREE_DEPLOY.md](docs/FREE_DEPLOY.md): Neon + Render + Cloudflare Pages, no card, no monthly cost.
