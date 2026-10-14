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
python scripts/seed_demo.py --api http://localhost:8000 --admin-key "$ADMIN_API_KEY"   # a demo business with a sales slump
python -m pytest tests -q -W ignore          # 357 tests; needs Postgres at DATABASE_URL
```

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
