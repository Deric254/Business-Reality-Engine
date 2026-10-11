# Business Reality Engine

Always-on business intelligence and decision system. Connects to a
business's existing systems, builds a unified model, monitors it, detects
what matters, explains why, alerts management, and builds institutional
memory of decisions and outcomes.

Full product blueprint: see `docs/blueprint.md`.
Security status: see `SECURITY.md` — read before adding any new connector
or endpoint. It tracks what's actually implemented vs. still owed, and
has caught a real bug (tenant isolation, CORS, error leaks) nearly every
time it's been followed through on — keep it current.

## Backend setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in DATABASE_URL, API_KEY, an LLM provider (optional)

createdb business_reality
alembic upgrade head

uvicorn app.main:app --reload
```

Every request except `/api/health` needs an `x-api-key` header matching
`API_KEY` in your `.env`.

### LLM provider (optional)

The reasoning layer (alert narratives, `/api/questions/ask`) works with
any of: Anthropic, OpenAI, or an OpenAI-compatible endpoint (Groq,
Together, local Ollama, etc). Set `LLM_PROVIDER` in `.env` — see
`.env.example` for the full set of vars per provider. Without one
configured, alert reasoning is skipped (severity-gated feature) and
`/api/questions/ask` falls back to a deterministic template — nothing
breaks, it just doesn't get the natural-language layer.

## Frontend setup

```bash
cd frontend
npm install
cp .env.example .env   # VITE_API_BASE + VITE_API_KEY, matching the backend
npm run dev
```

Opens on `http://localhost:5173`. The backend's `CORS_ORIGINS` env var
must include this origin (it does by default).

## Structure

```
app/
  models/       # SQLAlchemy models — core (tenants, events, entities), alerts, cases
  api/          # FastAPI routers
  core/         # config, DB session, auth, shared ingestion, detection, entity resolution, coverage, business memory
  connectors/   # per-source-type ingestion (file/CSV built; API/webhook/DB/SFTP not yet)
  reasoning/    # provider-agnostic LLM layer + alert reasoning
alembic/        # DB migrations
frontend/       # React + TypeScript + Tailwind Control Room
  src/pages/       # Dashboard (Section 33), Alert Detail (Section 35)
  src/components/  # KpiStrip, FilterBar, AlertRow, TopBar
  src/api/client.ts
docs/
  blueprint.md  # the full 57-section product blueprint
```

## Status

The full product loop (Section 4) is built and tested end-to-end against
real Postgres data, not mocks: connect → ingest → resolve entities →
detect deviation → explain why → AI reasoning → alert → act → remember →
measure outcome → retrieve history → ask questions directly. A React
Control Room frontend consumes it live.

Every stage is tenant-isolated, with that isolation proven by actual
attack attempts (cross-tenant reads/writes/merges), not just code review
— see `SECURITY.md`'s audit history for what testing caught that review
didn't.

**Not built yet:** forecasting (Section 43), scenario simulation (44),
external context integration (42), a real scheduler (detection currently
runs on manual trigger), and the production-hardening items in
`SECURITY.md` (self-monitoring, deployment model, backtesting,
localization). The frontend has not been visually verified in a real
browser — only that it builds clean and every API call it depends on
returns correct data.
