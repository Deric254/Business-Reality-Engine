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
The authors have not built these images by hand: `.github/workflows/deploy-smoke.yml` builds the compose stack and smoke-tests it in CI, so check that it is green and follow the runbook's first-deploy checklist before putting a customer on it.

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt pytest httpx aiosmtpd
cp .env.example .env                         # DATABASE_URL, API_KEY, ADMIN_API_KEY, JWT_SECRET_KEY, CONNECTOR_ENCRYPTION_KEY
createdb business_reality && alembic upgrade head
uvicorn app.main:app --reload                # API on :8000

cd frontend && npm install && npm run dev    # UI on :5173 (set VITE_API_BASE if the API is elsewhere)
python -m pytest tests -q -W ignore          # needs Postgres at DATABASE_URL (defaults to the business_reality database above)
```

## Licences and the master account
Every business comes in through a licence key: one key creates one business, once, and can be revoked at any time.
- **Create the master account (once):** sign in as yourself, then `POST /api/master/bootstrap` with header `x-admin-key: <ADMIN_API_KEY>` and body `{"email": "<your email>"}`. A **Licences** link then appears in the sidebar for you and nobody else (everyone else gets a 404 on `/api/master/*`).
- **Issue keys** on the Licences page (price per month, who it is for). A key is shown once and stored only as a hash. The customer opens `/activate`, enters the key, their business name, email and password, and is signed in. Two people submitting the same key at once create one business, never two.
- **Revoke** from the same page: the business is refused on its next request (within about 20 seconds across several servers without Redis; at once with it) and cannot sign in. Your own master licence cannot be revoked.
- **Money:** the page shows only what you have recorded as received (**Record payment**, with an optional reference such as the M-Pesa code; each payment extends *paid through* by a month). Monthly recurring, collected this month and all time are sums of that ledger. Charging the $1 automatically needs a payment provider account (Paystack, Stripe or similar); its webhook would call the same `licensing.record_payment`. Until then, payment is recorded by hand. With enforcement on, a paid licence that is more than `LICENCE_GRACE_DAYS` (default 7) past its paid period, or past activation if it never paid, is paused: its people cannot sign in or use the app, the data is kept, and recording a payment restores access at once. A free licence (price 0) is never paused.
- **Reinstate** a revoked licence from the same page (`POST /api/master/licences/{id}/reinstate`); access returns on the business's next request.
- **Switch it on:** set `LICENCE_ENFORCED=true` and redeploy. The migration gives every business that exists today a free active licence, so nobody is locked out when you do. With the switch off (the default) licences are issued and tracked but not required.

## Connecting data (the Data page)
The Data page opens on one question, *Where do your sales and expenses come from?*, with five plain choices. Each opens a short guided path, and nothing technical is shown until someone picks "Something else".
- **My business software** (smePro, Imara, Pharmacy ERP): pick the software, then three steps. Get the helper app on the computer that has it, paste the code the page shows, and watch the page turn green. The helper app only *reads* the software's data (see `agent/README.md`). If something is wrong, the page says what and what to do, in plain words.
- **M-Pesa payments:** see below.
- **A spreadsheet or file** (Excel, CSV, Google Sheets, an M-Pesa statement): see "What happens to your file".
- **My online shop** (WooCommerce, Shopify).
- **Something else:** live feed for developers, web link, database, SFTP, email, shared folder, and a custom helper app for technicians.

Once something is connected the page shows one line saying whether everything works, then **Your connections**, each with a plain status and everything else (pause, check again, new code or link, disconnect) under **Manage**. Check my data, merge duplicates and typing an entry by hand are under **More tools**.

## M-Pesa
Two ways in.
- **Live payments (Paybill or Till).** Choose **M-Pesa payments**, then **Create my M-Pesa link**, and follow the three steps on screen: (1) copy your link, (2) give it to Safaricom as the confirmation link for your Paybill or Till (through the Daraja portal or your Safaricom business contact), (3) press **Test my link**. The test shows what a sample payment would look like and adds nothing to your figures; the page then waits for your first real payment and tells you when it arrives. The link is shown once, for safety. If it is lost, **Manage, Get a new link** makes another and the old one stops working at once (`POST /api/connectors/{id}/new-link`). Each payment arrives as a sale at the time it happened (Daraja's `TransTime` is read as East Africa time), keyed on its `TransID`, so a payment delivered more than once is still counted once. The payer's number, as Daraja sends it (it may be masked or hashed), is stored as the customer. The reply is the one Daraja waits for (`ResultCode` 0). *Status: built to Daraja's documented format and tested with sample payloads; not yet tried against live Safaricom traffic, so make one small test payment first.*
- **Statements.** Choose **A spreadsheet or file** and upload the CSV or Excel statement. Paid in is read as sales and Withdrawn as expenses; the wizard says so and lets you change it. Rows that share a receipt number are all kept. Withdrawn includes everything that leaves the account, including transfers to your own accounts and settlements, which are not business costs. Under **Leave out money-out rows whose details contain** list the words that mark them (for example `settlement, transfer to`; not case sensitive). Money in is never left out, nothing is left out unless you list words, and every upload says how many rows and how much money were left out.

## What happens to your file
Nothing is taken blindly. Before anything is counted the wizard shows **What we found**, in plain words:
- Empty rows and empty columns are ignored and counted; rows keep the numbers the sheet shows, so any message points at the right row.
- A column is judged by the cells that have something in them, never by the empty ones, so a date column is still found when most rows are empty.
- A column with data but no header is listed and shown as "(no name) for example “Sugar”", so it can be picked.
- Cells that cannot be read as a date or a number, and rows with no amount, are counted up front and listed after the import.
- A date or an amount found by looking at the contents rather than the name is marked **a guess** and must be checked. Counts, stock levels, ages, phone numbers and identifiers are never guessed to be money.
- **A file with no date column** asks *When did these happen?* and offers one day for the whole file, or a column that holds the date. Every row is then dated that day at midday on the business's own clock (`default_date`).
- A file that is only a list (no date and no money, such as products and stock) is not connected: it says so, because this app looks at money coming in and going out over time.

## Sample data and demo businesses
- **Try before connecting.** A business with no data sees "Want to see it working first?" on the Data page: a neighbourhood shop, a pharmacy or a workshop, each with about four months of made-up activity and one problem hiding in it (a branch gone quiet; stock buying that has nearly doubled; one customer behind half the invoices and a very large bill). Owners only, and only into a business with no data. It can be removed while it is the only data there (`/api/demo/samples`); real data is never touched.
- **Demo businesses for showing the app.** On the Licences page, "Create demo businesses" makes one business per sample, each with an owner login (the password is shown once) and a free licence that can be revoked and reinstated like any other.
- **From the command line:** `python scripts/seed_demo.py --api http://localhost:8000 --admin-key $ADMIN_API_KEY --kind duka` (or `pharmacy`, `workshop`).

## Periods: one definition of "last N days"
`app/core/periods.py` is the only place a period is defined: N **complete** days on the business's own calendar, ending **yesterday**, compared with the N days before them. Today is left out because it is not over, so a steady business never looks like it is falling in the morning. "Today" on its own is the day so far and is never presented as a verdict. **All time** runs from the first sale or expense to the end of today with nothing to compare. The Overview, the chart, the health strip, Ask, Check my data and the alerts all take their boundaries from it (the alerts use the same window, so an alert's "7 days" and the Overview's agree), and the chart groups by the business's day, never the UTC day.

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

## Reads data as the business keeps it
Nobody has to reshape their files. On connect, the columns are detected (by an AI when a provider key is set, by rules otherwise; every AI answer is checked against the real columns) and the person only confirms.
- **Report titles above the header** (business name, date range) are skipped; error messages still quote the row number as the sheet shows it.
- **Money in / money out in separate columns** (M-Pesa and bank statements, debit/credit ledgers): money in is a sale, money out is an expense. These are interpretations, so the wizard says so and the person can change them.
- **No total column**: the amount is quantity × unit price. A missing price leaves the amount empty; it is never invented.
- **A Type column** (Sale, Purchase, Withdrawal...) decides sale or expense per row.
- Dates day-first, amounts such as `KES 1,500` or `1,500/=`, blank and TOTAL rows: see below.
Not handled yet: dates or months spread across columns, and one workbook with several tabs as one source (connect each tab separately).
- A source that reuses one id for several lines (an M-Pesa statement gives a charge and its transfer the same receipt number; an invoice number covers every line) keeps every line that says something different. Exact copies of a line still count once.

## One shared dataset
Every source (file, sheet, link, database, webhook, typed entries) is normalised into the same event table, so every total, alert and chart reads one place.
- **No double counting across sources.** If a second source brings a record with the same id (a receipt or order number), the same amount and the same day as one already counted, it is kept for provenance, marked `duplicate`, and left out of every total. Records without a shared id are never merged, because two real sales cannot be told apart safely.
- **Same sales in two sources with no shared id** (a POS and an M-Pesa statement): the engine pairs records one-to-one by amount and moment (within 15 minutes, or the same day for sources that hold dates only) and, when enough match, suggests counting them once. The owner confirms (`POST /api/connectors/{other}/merge-into/{keep}`); the richer source (more branch/product/customer detail) is suggested as the main record. After a merge, records still arriving are paired automatically in either load order, records with no partner stay counted and are listed per source ("only in M-Pesa: 3 payments, 2,664") so unrecorded sales or missing payments are visible, and `DELETE /api/connectors/{other}/merge` undoes it exactly. Nothing is ever merged without that confirmation.
- **One spelling per place.** "nairobi", "Nairobi " and "NAIROBI" are one location; the spelling already in the business wins.
- **Messy sheets are read sensibly.** `03/09/2026` is 3 September unless the column proves month-first; amounts like `KES 1,500`, `1,500/=` or `(1,500)` are numbers and a dash means "nothing"; blank rows are ignored. A stray row (a TOTAL line, a note) is skipped and named in the import summary instead of rejecting the whole sheet, but if most rows are unreadable the import fails, because the column mapping is probably wrong.
- **One currency.** Amounts are converted to the business's base currency on the way in.
- **Disconnect removes a source and its data** (`DELETE /api/connectors/{id}`, owner only, audit-logged). A record that another source also held stays counted. Use Pause to stop a source without deleting anything.
- The Data page shows, per source, how many records are counted, how many were already counted from another source, and how many were rejected (`GET /api/connectors/contributions`).

## Database sources
Postgres, SQLite and MySQL (MariaDB) are tried against live servers (`tests/test_mysql_live.py` runs against a real MySQL or MariaDB server whenever `MYSQL_TEST_URL` is set, and CI sets it). SQL Server, Oracle and generic ODBC are wired but have not been tried against a live server; `GET /api/connectors/database-types` reports which are which. Every database query must be a single read-only SELECT, the session is set read-only where the database supports it, and SQL Server and ODBC have no such switch, so give those a read-only login.

## Signing in, and installing the app
Sign-in lasts for the tab by default. **Keep me signed in on this device** (a checkbox on the sign-in form) keeps it on that device until the person signs out or the refresh token expires (7 days of not using the app); use it only on your own phone or computer, because the tokens then sit in the browser's local storage rather than the tab's. The app is installable ("Add to Home screen" or "Install"): it opens quickly from an icon and shows its last start page when offline, but it never caches data, and the API is never served by the offline worker (`frontend/public/sw.js`).

## Checking your own setup
Things the authors could not try from outside your server, as one-line checks:
- `python scripts/mpesa_simulate.py --url <your M-Pesa source link> --amount 150 --count 3 [--repeat]` sends payments shaped like Daraja's confirmations and prints Safaricom-style acceptance; a `--repeat` payment is counted once.
- `python scripts/check_ai.py --kind anthropic --key sk-...` asks your AI provider for the small JSON plan the app asks for and says whether it parsed. The key is never printed.
- `python scripts/seed_demo.py --api URL --admin-key KEY --kind duka` makes a demo business; `scripts/bench_ingest.py` and `scripts/load_test.py` measure your machine.

## Time and time zones
A time that carries a zone (`…Z`, `…+03:00`) is converted to UTC. A time with **no** zone is read on the business's own clock: spreadsheet cells, typed entries, and API events sent without an offset. An API caller that sends UTC clock times without an offset will therefore see them shifted by the business's offset, so always send the offset. A webhook event with no time at all is stamped with the moment it arrived. Days start at the business's local midnight.

## Performance
Measured by the author on one business with about 860,000 records from three sources (a POS, an M-Pesa statement, an accounting export), PostgreSQL on a small machine. Several runs are on record and they differ with the machine and its load, so each figure is a range.

| Operation | Time |
|---|---|
| Dashboard numbers (30 and 365 days) | 0.3 to 1 s |
| Business health, source contributions | 0.4 to 1.1 s |
| Plain question, weekday, forecast | about 0.1 s |
| AI-planned question (grouping, comparison over a year) | 1 to 2 s |
| Full detection cycle | 3 to 8 s |
| Upload of 5,000 rows | 3 to 6 s (one row: under 0.1 s) |
| Suggest merges between sources (cached 5 min) | about 4 s |
| Confirm a merge that pairs 320,000 records (one time) | 30 to 50 s |
| Undo that merge | 15 to 30 s |
| Disconnect a 400,000-record source | 3 to 7 s |

Re-run for the 1.0 audit on a single vCPU with 4 GB and no tuning: bulk ingestion about 4,800 events a second (5,000 events in about 1 s; re-sending the same 5,000 as duplicates about 3,200 a second). One event at a time, which is what a single-event webhook does, runs at about 105 a second, so send batches when you can.

How it stays fast: aggregation and matching happen in the database, never by loading records into Python; reconciliation is set-based and incremental, only against records near new arrivals, and uses grouping instead of joins so a stale query plan after a bulk import cannot turn it quadratic; per-business lookups (place spellings, branch and product names) live in small tables or short caches, so no request scans the whole history; matching works from small temporary tables with fresh statistics.

Not measured: more than about a million records in one business, many hundreds of businesses in one database, or more than about 8 sources compared pairwise (suggestions stop there). The events table is not partitioned; at tens of millions of rows per business, partition it by tenant and month.

## Who calls what

| Caller | Credential | Reaches |
|---|---|---|
| People in the web app | per-user JWT (owner or staff) | everything in their own business; owners change settings |
| Connected systems | per-connector webhook token; service key `x-api-key` for event ingestion only (off by default in production: set `ALLOW_SERVICE_KEY_INGEST=true` to allow it) | `/api/webhooks/...`, `/api/events/...` |
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
tests/          the test suite                   .github/        CI
```

## Free hosting
See [docs/FREE_DEPLOY.md](docs/FREE_DEPLOY.md): Neon + Render + Cloudflare Pages, no card, no monthly cost. This suits demos and a first pilot. Free tiers can sleep when idle, and a sleeping server cannot run the scheduled checks that notice a source gone quiet, so put paying customers on an always-on server.
