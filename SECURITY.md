# Security — Business Reality Engine

Tracks what's actually implemented vs. still owed. Update this file in the
same commit as any security-relevant change — don't let it drift from the code.

## In place

1. **Multi-tenant isolation** — every data table carries `tenant_id`. All
   queries filter by it. `ingest_normalized_event()` additionally verifies
   the connector used actually belongs to the claimed tenant before writing
   anything (`app/core/ingestion.py`) — proven live: cross-tenant ingest
   returns 403. All alert endpoints (`app/api/alerts.py`) verify tenant
   ownership via `_get_owned_alert()` — proven live: cross-tenant GET and
   PATCH both return 404 with zero mutation. This check was MISSING on
   alert endpoints until it was caught while building the detail endpoint —
   worth remembering that "isolation exists elsewhere in the codebase"
   doesn't mean it exists on every endpoint; check each one.
2. **Service-level API key** — every endpoint except `/api/health` requires
   `x-api-key` (`app/core/auth.py`). Boot refuses to start in production
   without `API_KEY` set (`app/core/config.py`). With only that key, event
   ingestion is refused in production unless `ALLOW_SERVICE_KEY_INGEST=true`
   (see "Added with the audit follow-up").
3. **No SQL injection surface** — all queries go through SQLAlchemy's ORM
   with bound parameters. No raw string-interpolated SQL anywhere.
4. **No leaked internals on error** — global exception handlers
   (`app/main.py`) catch DB-layer errors and return generic messages;
   full detail goes to the server log only, never the response body. The
   same principle is applied in `app/reasoning/llm_reasoning.py` — any LLM
   provider SDK exception (network, auth, rate-limit) is caught, logged in
   full server-side, and returns only a sanitized message to the client.
   This was also a gap initially: the first version let a raw
   `openai.PermissionDeniedError` (with host/config details) reach the
   client as a 500 before it was caught in testing.
5. **File upload hardening** (`app/connectors/file_connector.py`):
   extension allowlist, 10MB size cap, 50k row cap, no formula execution
   from XLSX, explicit column mapping (never trusts uploaded column names
   as identifiers).
6. **Secrets not committed** — `.env` is gitignored; `.env.example`
   documents required vars without real values.
7. **Webhook auth is deliberately NOT the service API key.** `/api/webhooks`
   is the one router mounted without `require_api_key` — external systems
   posting webhooks have no way to know that key. Instead, each webhook
   connector gets its own secret token (SHA-256 hashed at rest, shown in
   plaintext exactly once at creation, never retrievable again — verified
   live). If you're auditing routers for "does this require the service
   key," this is the one intentional exception; everything else should
   have it.
8. **Connector credential encryption** (`app/core/secrets.py`) — Fernet
   symmetric encryption for any connector config field in
   `SECRET_CONFIG_KEYS` (currently: password, db_password, api_secret,
   private_key). Key lives in `CONNECTOR_ENCRYPTION_KEY`, outside the
   database. Verified live: a database connector's stored config shows a
   Fernet ciphertext, not the plaintext password. Decrypted only at the
   moment of actually connecting (`database_connector.fetch_rows`), never
   returned in any API response — `ConnectorOut` doesn't even include
   the `config` field at all, so this can't leak through that path either.
9. **System self-monitoring** (`app/api/system.py`) — `/api/system/*` is
   the second intentional exception (after `/api/tenants`) to "every
   endpoint scoped by tenant_id": `connector-health` and
   `scheduler-runs` deliberately span ALL tenants, because that's the
   point of an ops view. Now gated behind a SEPARATE admin credential
   (`ADMIN_API_KEY`, sent as `x-admin-key`), NOT the general service API
   key — holding `API_KEY` alone (which every tenant-scoped router
   accepts) is no longer sufficient to see cross-tenant connector or
   scheduler data. Fails CLOSED (503) if `ADMIN_API_KEY` isn't
   configured at all, deliberately unlike the general key's dev-mode
   fallback — a new sensitive endpoint category should default to
   unavailable, not open. Verified live: unconfigured → 503, general key
   alone → 401, correct admin key → 200, wrong admin key → 401. (The
   code for this landed in commit c0cfcd2, bundled with an unrelated
   migration fix under a commit message that only describes the
   migration — a `git add -A` lapse, noted here for traceability rather
   than by rewriting already-packaged history.)
10. **Rate limiting** (`app/core/rate_limit.py`) — hand-rolled
    FixedWindowLimiter, NOT slowapi (tried it first; silently didn't
    work — see the commit message for the full diagnosis, worth reading
    if choosing a rate-limit library for any future FastAPI work here,
    since the incompatibility is with FastAPI's routing internals, not
    anything specific to this app). Two limits: 300/min per source IP
    applied to every route, 100/min per webhook connector checked
    explicitly in the handler. Verified live with real request floods:
    exact limits, exact block points, and confirmed per-connector
    isolation (one connector's exhaustion never affects another's, even
    from the same source IP).
    **Update:** when `REDIS_URL` is set, both limiters use a shared
    Redis counter (INCR+EXPIRE), so the limit holds across workers and
    machines (verified: two real processes, 60+60 attempts, exactly 100
    allowed). Redis runtime failure FAILS OPEN (logged) so an outage
    doesn't take the API down. `TRUSTED_PROXY_HOPS` enables
    X-Forwarded-For parsing behind a reverse proxy; leave 0 otherwise
    (the header is client-spoofable). Fixed-window: bursts at a window
    boundary can reach ~2x the limit.
    The scheduler uses a Redis `SET NX EX` lock with atomic Lua
    compare-and-delete release, so N workers run one detection cycle
    (verified: second acquire refused, re-acquire after release).
11. **Per-tenant user auth — partial, real, tested** (`app/core/
    auth_users.py`, `app/api/auth.py`). Bcrypt password hashing, JWT
    access (30min) + refresh (7day) tokens, role-based (`owner`/`staff`).
    The pattern is `optional_user_check()`: no token falls back to
    existing service-key-only behavior (verified unaffected each time
    it's extended to a new router), a present token must be fully
    valid — correct type, unexpired, tenant-matched, role-checked where
    required — or the request is rejected, never silently falls back.
    Now applied to every tenant-scoped router: `connectors` (owner-role
    only for creation — credentials live here), `alerts`, `entities`
    (including `merge` — the endpoint with the worst security history in
    this codebase, now with two independent layers of tenant
    protection), `cases`, `tenants.list` (the original stopgap, now
    closed), `coverage`, `detection`, `questions`, `forecasting`,
    `simulation`, and `events` (including the batch-ingest case: a
    request spanning multiple tenants is rejected up front rather than
    partially processed). No role restriction on any of these except
    connector creation. `system.py` is deliberately EXCLUDED — it's
    cross-tenant by design (an ops view across every tenant), so this
    per-tenant pattern is the wrong tool for it; still needs a separate
    admin-only credential, see "Still owed" item 1.

## Still owed — do not treat as done

1. **Per-tenant user authentication — closed (mandatory JWT).** Every
   user-facing route enforces `authorize_tenant`: no token -> 401, wrong
   tenant -> 403, refresh token used as access -> 401. The shared service
   key is accepted ONLY by `/api/events/*` (machine ingestion) and never
   grants read access to tenant data. `/api/auth/register` needs the admin
   key or an owner token of that tenant; login/refresh are public but
   throttled (10/min/IP). The browser bundle contains no API key. Production boot
   refuses to start without API_KEY, a >=32-char JWT_SECRET_KEY and
   CONNECTOR_ENCRYPTION_KEY. Covered by `tests/`. Earlier
   versions of this file called this closed while JWT was opt-in and
   several connector endpoints had no auth at all; both are fixed.
   **Closed since:** token revocation, single-use refresh tokens, account lockout, an
   audit log and security headers (see "Added with the security hardening pass").
   **Still owed:** no password reset (email flow); the frontend keeps tokens in
   sessionStorage (readable by injected script; the nginx CSP is the mitigation);
   no user invitation/management UI.
2. **Encryption at rest** for the database — not configured yet; depends on
   final hosting choice (Section 52).
3. ~~Rate limiting~~ — **closed**, see "In place" item 10. Shared
   across workers when `REDIS_URL` is set; in-memory (per-process)
   otherwise. Fixed-window, fails open on Redis errors.
4. ~~Secrets management for connector configs~~ — **closed**, see "In place"
   item 8. Remaining nuance: key rotation isn't handled — rotating
   `CONNECTOR_ENCRYPTION_KEY` would make every previously-stored credential
   undecryptable. Not urgent at current scale (one key, one deployment)
   but needs a re-encryption migration path before rotation is ever needed.
5. ~~Audit logging~~ — **closed**: security-relevant actions are written to the audit log (`app/core/audit.py`; see "Added with the security hardening pass").
6. **Dependency scanning** — `requirements.txt` versions are pinned; `pip-audit` and `npm audit` were clean at the last audit (docs/PROGRESS_REPORT.md). Re-run before each release.
7. **Tenant-ownership audit — completed.** Swept every endpoint with an id
   path param across all routers. Found and fixed 3 real gaps this way,
   not from design review: alert endpoints, `/api/entities/merge`
   (missing tenant_id entirely), and `/api/connectors/{id}`. All 9
   id-based endpoints now confirmed to filter by tenant_id and return 404
   (not 403) on mismatch. Re-run this sweep any time a new router with an
   id-based endpoint is added — it has caught a real bug every time so far.

## Added with onboarding / alert delivery

- **Webhook SSRF guard** (`app/core/notifications.py`): https only; the host
  must resolve to public addresses (no loopback, private, link-local, metadata
  IPs), no embedded credentials, no redirects followed. Checked at channel
  creation and again at send time. Residual risk: DNS rebinding between the
  check and the connection; run workers with egress restricted to the public
  internet to close it fully. `ALLOW_PRIVATE_WEBHOOK_TARGETS=true` disables the
  guard and is for dev/tests only.
- Webhook payloads are HMAC-SHA256 signed (`X-BRE-Signature`, over
  `timestamp.body`); the secret is generated server-side, shown once, stored
  Fernet-encrypted. Receivers should reject old timestamps and dedupe on the
  alert id (delivery is at-least-once).
- Email subjects are collapsed to one line (header-injection safe). Payloads
  export title/summary/action only, never raw evidence.
- Metric `field` is whitelisted (`amount`, `quantity`); it was previously an
  unvalidated `getattr` on the event model.
- Tenant creation is admin-key only (`POST /api/system/tenants`).

## Added with the detection upgrade
- `POST /api/detection/run/all` requires an owner token of that tenant (403 other tenants, 401 no token).
- Monitored-metric `direction` is an enum and `absolute_threshold` is bounded (0..1e13); invalid values return 422.
- Alert refresh preserves the escalation stamp (regression-tested), so an ignored alert cannot silently de-escalate.
- Escalation notifications are idempotent per (alert, channel, kind) and respect each channel's severity floor.

## Added with the security hardening pass
- **Token revocation.** Every token carries a `jti` and a millisecond issue time. Logout revokes the access token (checked on every request in Redis, or in-process without Redis, so it dies immediately) and the refresh token. `POST /api/auth/logout-all` and a password change invalidate everything issued to that user up to that instant. Tokens minted before this feature (no `jti`) are rejected.
- **Single-use refresh tokens.** Each refresh retires the presented token (recorded in `revoked_tokens`, the authoritative store, atomically). Presenting an already-used refresh token is treated as theft: all of that user's tokens are revoked and an audit entry is written. Concurrent refreshes of one token: exactly one wins (tested).
- **Account lockout.** 5 failures per email in 15 min locks that email for 15 min (`LOGIN_MAX_FAILURES`, `LOGIN_FAILURE_WINDOW_SECONDS`, `LOGIN_LOCK_SECONDS`); counted identically for unknown emails, so there is no enumeration signal. Success resets the counter. Stated trade-off: someone who knows an email can keep it locked for short periods (nuisance, not a breach). This sits on top of the 10/min per-IP throttle.
- **Password change** (`POST /api/auth/change-password`) needs the current password, revokes all sessions, and returns a fresh pair. Passwords over bcrypt's 72-byte limit are rejected with 400 (previously a 500 / silent truncation risk).
- **Audit log** (`audit_log`, append-only, `GET /api/audit/`, owner only, tenant-scoped): logins, failures, lockouts, logouts, password changes, refresh-reuse, registrations, connector/channel/metric changes, entity merges, alert state changes, tenant creation. Details never include secrets or tokens. A failed audit write is logged and never fails the request.
- **Response headers** on every API response: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, a deny-all `Content-Security-Policy` (not on /docs), `Cache-Control: no-store` on /api/auth, HSTS in production.
- **Limits, stated:** without Redis, revocation/lockout state is per process and lost on restart (a revoked *access* token could live up to 30 min after a restart; refresh tokens and sign-out-everywhere remain enforced from the database). Read-only actions are not audited. Existing sessions end when this is deployed (old tokens lack `jti`).

## Added with user management
- `/api/users/*` is owner-only and tenant-scoped; another tenant's user ids answer 404, exactly like a missing one.
- Owners create accounts with a generated one-time temporary password (~84 bits, shown once, stored only as a bcrypt hash, never logged or audited). The account's tokens carry a `mcp` claim: every endpoint except change-password, logout and refresh answers 403 until the password is changed (enforced server-side, not just in the UI).
- Guards: a business always keeps at least one owner; nobody removes, demotes or resets themselves; role change, reset and removal revoke that user's sessions immediately; all are audited.
- **Password recovery and invitations (built).** Tokens are 256-bit random, single-use, expiring (reset 60 min, invite 7 days); only their SHA-256 is stored; a newer token voids older ones; spending is one atomic UPDATE (concurrent use: exactly one wins). `forgot-password` answers identically for known and unknown addresses, sends mail in a background task (no timing signal), is limited per IP and 3/hour per address, and a bad new password does not burn the token. A reset revokes every session and clears lockout. Invited accounts hold an unusable random password until accepted. Residual risk: anyone who can read the recipient's mailbox can reset; keep SMTP credentials and `APP_BASE_URL` (links are built from it) correct. If SMTP is not configured, forgot-password reports `email_enabled: false` and sends nothing, and invitations are refused (use temporary passwords); people recover with security questions (below), and the holder of the admin key can issue a one-time reset link through `POST /api/system/reset-link` (audit-logged).
- **Security questions (built).** Recovery that needs no email. A person picks 3 questions from a fixed list; every answer must be right. Answers are normalised (case, spacing, accents), SHA-256 digested, then bcrypt-hashed, so nothing readable is stored. Setting them needs the current password. `recovery-questions` returns a repeatable decoy set for unknown emails and for accounts with none set, and `recover-with-answers` always performs the same number of bcrypt checks, so neither reveals which accounts exist; failures are limited per IP and locked per email (5 in 15 minutes) separately from sign-in. Success revokes every session. Residual risk: answers are still guessable by someone who knows the person (a relative, a former colleague); treat this as weaker than email and encourage both.
- **Delivery targets.** Slack, Teams and custom-webhook URLs go through the same SSRF guard (https only, no private/loopback/link-local, re-checked at send time, no redirects). SMS/WhatsApp numbers must be E.164 and are sent through Twilio with credentials from the environment only; stored errors never include credentials.

## Added with the audit follow-up
- **The shared service key cannot write events in production.** One key serves every business, so anyone holding it, plus a business's tenant and connector ids, could write records into that business. `/api/events/*` now needs a business sign-in or a connector's own webhook token when `ENV=production`. `ALLOW_SERVICE_KEY_INGEST=true` brings the old behaviour back for a trusted single-business install; outside production it stays allowed so development and tests are unchanged. Residual risk: the key is still required on those routes as a second gate, so keep it secret.
- **Connector SSRF guard is on by default in production.** Database, SFTP, IMAP and URL connectors refuse private, loopback, link-local and metadata addresses when `ENV=production`, in every deployment (before, only `render.yaml` set it). `BLOCK_PRIVATE_CONNECTOR_HOSTS=false` is the explicit opt-out for a single-business install that must reach its own network. Same residual risk as the webhook guard: DNS rebinding between the check and the connection.
- **Payment gates access.** With `LICENCE_ENFORCED=true`, a paid licence past its grace period (`LICENCE_GRACE_DAYS`, default 7) is refused like a revoked one until a payment is recorded. A revoked licence can be reinstated by the master account only (404 for everyone else, audit-logged).
- **Demo businesses.** `POST /api/master/demo-businesses` is master-only. Passwords are generated server-side with `secrets`, returned once in that response and never stored in plain text. Their licences are free and revocable. The sample-data endpoints (`/api/demo/samples`) are owner-only per business and refuse to run on a business that has data.
- **Live M-Pesa link.** The private confirmation link carries the connector's webhook token like any webhook connector (hashed, constant-time compared, throttled). Daraja does not sign its callbacks, so the link's secrecy is the protection: register it only with Safaricom, and replace the connector if it leaks.
- **"Keep me signed in on this device" is opt-in.** By default tokens live in the tab's session storage. If a person ticks the box, access and refresh tokens are kept in local storage, which injected script could read, until they sign out or the refresh token expires. The Content-Security-Policy and the absence of untrusted HTML are the defence, and the choice is shown with a warning to use it only on one's own device. Signing out clears both stores; changing a password revokes every session as before.
- **The service worker never handles `/api/` and never caches data.** It caches only the start page (served network-first, so a new release is picked up at once) and hash-named static files. `sw.js` and the manifest are served with `Cache-Control: no-cache`.

