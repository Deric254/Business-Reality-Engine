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

## Ask anything, with numbers you can trust
- **Common questions need no AI** (profit, sales, expenses, top customers, branches, best weekday, forecast, what needs attention).
- **Everything else is planned by the AI and answered by SQL.** With an AI provider connected, a question with a filter, a grouping or a comparison ("best products at Maua last month", "sales by week against last year", "average sale this week") is turned into a query plan: a small JSON object where every field is one of a fixed set of choices. The plan is validated, branch and product names must exist in the business's records, and then it runs as ordinary SQL. The AI never sees or writes a number; the answer states exactly what was computed (period, filters, comparison), so a misread question is visible. Customer names are never sent to the AI.
- **Days start at the business's local midnight** (tenant timezone), for "today", weekday and daily groupings.
- **Forecasts follow the weekly rhythm.** The next 7 or 30 days are projected day by day from the business's own pattern (a Saturday is not a Tuesday; a closed Sunday stays at zero) plus a trend, with a likely range from how far past days missed. It needs four weeks of history; with less, the older straight-line projection is used. Holidays and paydays are not modelled in the forecast.

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
