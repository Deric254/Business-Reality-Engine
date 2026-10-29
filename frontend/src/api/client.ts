import { getTokens, setTokens, type TokenPair } from "./session";

// API base is configured at build/run time. No service API key lives in the
// browser bundle: users authenticate with their own per-tenant token.
// Production: the web app and API are one service, so calls are same-origin (empty base). Dev: the API on :8000.
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "http://localhost:8000" : "");

// A request that gets no answer for this long is abandoned with a clear message, instead of spinning forever.
const REQUEST_TIMEOUT_MS = 45_000;

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => ({ detail: res.statusText }));
  const detail = typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`;
  return new ApiError(res.status, detail);
}

// One in-flight refresh shared by concurrent 401s, so a burst of requests
// with an expired access token triggers a single refresh, not many.
let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const tokens = getTokens();
  if (!tokens) return false;
  if (!refreshing) {
    refreshing = fetch(`${API_BASE}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: tokens.refresh_token }),
    })
      .then(async (res) => {
        if (!res.ok) return false;
        setTokens((await res.json()) as TokenPair);
        return true;
      })
      .catch(() => false)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

async function request<T>(path: string, options: RequestInit = {}, retried = false): Promise<T> {
  const tokens = getTokens();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {}),
        ...options.headers,
      },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") throw new ApiError(0, "The server took too long to answer. Please try again.");
    throw e;
  }
  if (res.status === 401 && tokens && !retried) {
    if (await refreshTokens()) return request<T>(path, options, true);
    setTokens(null); // session is dead — listeners send the user to sign-in
  }
  if (!res.ok) throw await parseError(res);
  return res.json();
}

/** Downloads a binary file (the Analytics PDF): same token and one sign-in retry as request(), but returns the Blob. */
async function requestBlob(path: string, retried = false): Promise<Blob> {
  const tokens = getTokens();
  const res = await fetch(`${API_BASE}${path}`, {
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    headers: tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {},
  });
  if (res.status === 401 && tokens && !retried) {
    if (await refreshTokens()) return requestBlob(path, true);
    setTokens(null);
  }
  if (!res.ok) throw await parseError(res);
  return res.blob();
}

export async function login(email: string, password: string, remember = false): Promise<void> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await parseError(res);
  setTokens((await res.json()) as TokenPair, remember);
}

export async function changePassword(current: string, next: string): Promise<void> {
  const pair = await request<TokenPair>("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: current, new_password: next }),
  });
  setTokens(pair); // every old session was revoked; this is the fresh one
}

export function logout() {
  const tokens = getTokens();
  setTokens(null); // the UI signs out at once; revoking on the server is best-effort and never blocks it
  if (tokens) {
    fetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokens.access_token}` },
      body: JSON.stringify({ refresh_token: tokens.refresh_token }),
    }).catch(() => undefined);
  }
}

export interface Tenant {
  id: string;
  name: string;
}

export interface Alert {
  id: string;
  severity: "critical" | "high" | "medium" | "informational" | "opportunity";
  state: string;
  title: string;
  what_happened: string;
  confidence: number | null;
  data_coverage_pct: number | null;
  recommended_action: string | null;
  priority_rank?: number | null;
  correlated_alert_ids?: string[] | null;
  escalated?: boolean;
  period_label?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AlertDetail extends Alert {
  related: { id: string; title: string }[];
  when: { period_start: string | null; period_end: string | null };
  where: Record<string, unknown> | null;
  evidence: Record<string, unknown>;
  related_variables: Record<string, { current: number; baseline: number; change_pct: number | null }> | null;
  possible_drivers: { driver: string; evidence: string }[];
  ruled_out: { driver: string; evidence: string }[];
  ai_summary: string | null;
  historical_experience: {
    case_id: string;
    when: string;
    decision: string | null;
    intervention: string | null;
    outcome: string | null;
    attribution_confidence: string | null;
  }[];
  created_at: string;
  updated_at: string;
}

export interface PeriodInfo { days: number; all_time: boolean; first_day: string; last_day: string; timezone: string; includes_today: boolean; today: string }
export interface KpiBreakdown {
  period_days: number;
  period: PeriodInfo;
  as_of: string;
  revenue: { current: number; previous: number };
  expenses: { current: number; previous: number };
  profit: { current: number; previous: number; change: number; change_pct: number | null };
  contributors: { name: string; delta: number; delta_pct: number | null; profit_impact: number }[];
  largest_contributor: string | null;
}

export interface QuestionAnswer {
  question: string;
  answer: string;
  evidence: Record<string, unknown>;
  answer_source: string;
  intent: string | null;
  answered: boolean;
  ai_connected?: boolean;
  suggestions?: string[];
  /** Pages a web answer was written from (answer_source "web"), numbered as the answer cites them. */
  sources?: { title: string; url: string }[];
  /** The exact words searched (web answers), and whether the owner has not yet said where the business trades. */
  search_query?: string;
  needs_area?: boolean;
}

/** Why the dashboard shows what it shows: what each source read, counted and left out, and what is stopping anything showing. */
export interface CheckFinding { level: "problem" | "warning" | "info" | "ok"; source: string | null; text: string; action: string | null }
export interface CheckSource {
  id: string; name: string; type: string; active: boolean; counted: number; rejected: number; matched: number;
  first_record: string | null; last_record: string | null; in_view: number; last_checked: string | null; status: string | null; error: string | null;
}
export interface DataCheckResult {
  verdict: "problem" | "attention" | "ok"; headline: string; findings: CheckFinding[]; sources: CheckSource[];
  range: { counted: number; first: string | null; last: string | null; in_view: number; period_days: number; suggest_days: number | null };
  ai_connected: boolean; schema: { current: boolean; database: string | null; expected: string };
}

export interface CoverageRow {
  area: string;
  status: "live" | "recent" | "stale" | "not_connected";
  last_data_at: string | null;
  checked_at: string;
}

export const api = {
  listTenants: () => request<Tenant[]>("/api/tenants/"),

  runChecks: (tenantId: string) => request<{ alerts: { id: string | null; title: string | null }[]; errors: number }>("/api/detection/run/all", { method: "POST", body: JSON.stringify({ tenant_id: tenantId }) }),
  listAlerts: (tenantId: string, filters: { severity?: string; state?: string } = {}) => {
    const params = new URLSearchParams({ tenant_id: tenantId });
    // Skip unset filters: URLSearchParams would otherwise send the literal string "undefined".
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v);
    return request<Alert[]>(`/api/alerts/?${params}`);
  },

  getAlertDetail: (alertId: string, tenantId: string) =>
    request<AlertDetail>(`/api/alerts/${alertId}/detail?tenant_id=${tenantId}`),

  updateAlertState: (alertId: string, tenantId: string, state: string) =>
    request<Alert>(`/api/alerts/${alertId}/state?tenant_id=${tenantId}`, {
      method: "PATCH",
      body: JSON.stringify({ state }),
    }),

  askQuestion: (tenantId: string, question: string, periodDays = 30) =>
    request<QuestionAnswer>("/api/questions/ask", {
      method: "POST",
      body: JSON.stringify({ tenant_id: tenantId, question, period_days: periodDays }),
    }),

  getKpis: (tenantId: string, periodDays = 30) =>
    request<KpiBreakdown>(`/api/questions/kpis?tenant_id=${tenantId}&period_days=${periodDays}`),

  getCoverage: (tenantId: string) => request<CoverageRow[]>(`/api/coverage/?tenant_id=${tenantId}`),
};

export type ChannelType = "email" | "webhook" | "slack" | "teams" | "sms" | "whatsapp";

export interface Channel {
  id: string;
  channel_type: ChannelType;
  target: string;
  label: string | null;
  min_severity: string;
  enabled: boolean;
  quiet_start: string | null;
  quiet_end: string | null;
  digest_mode: "off" | "daily";
  digest_hour: number;
  filter_kinds: string[] | null;
  filter_entities: string[] | null;
  signing_secret?: string | null;
}

export type ChannelPrefs = Partial<Pick<Channel, "quiet_start" | "quiet_end" | "digest_mode" | "digest_hour" | "filter_kinds" | "filter_entities">>;

export interface Metric {
  id: string;
  entity: string;
  field: string;
  period_days: number;
  baseline_periods: number;
  threshold_pct: string | number;
  direction: "drop" | "rise";
  absolute_threshold: string | number | null;
  enabled: boolean;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const settingsApi = {
  listChannels: (t: string) => request<Channel[]>(`/api/notifications/channels?tenant_id=${t}`),
  createChannel: (t: string, c: { channel_type: string; target: string; min_severity: string } & ChannelPrefs) =>
    request<Channel>("/api/notifications/channels", { method: "POST", ...json({ tenant_id: t, ...c }) }),
  updateChannel: (t: string, id: string, patch: Partial<Pick<Channel, "enabled" | "min_severity">> & ChannelPrefs & { clear_quiet_hours?: boolean; clear_filters?: boolean }) =>
    request<Channel>(`/api/notifications/channels/${id}?tenant_id=${t}`, { method: "PATCH", ...json(patch) }),
  deleteChannel: (t: string, id: string) =>
    request<{ deleted: boolean }>(`/api/notifications/channels/${id}?tenant_id=${t}`, { method: "DELETE" }),
  testChannel: (t: string, id: string) =>
    request<{ ok: boolean; error?: string }>(`/api/notifications/channels/${id}/test?tenant_id=${t}`, { method: "POST" }),
  listMetrics: (t: string) => request<Metric[]>(`/api/monitoring/metrics?tenant_id=${t}`),
  createMetric: (t: string, m: { entity: string; field: string; period_days: number; threshold_pct: number; direction: "drop" | "rise"; absolute_threshold: number | null }) =>
    request<Metric>("/api/monitoring/metrics", { method: "POST", ...json({ tenant_id: t, ...m }) }),
  updateMetric: (t: string, id: string, patch: { enabled: boolean }) =>
    request<Metric>(`/api/monitoring/metrics/${id}?tenant_id=${t}`, { method: "PATCH", ...json(patch) }),
  deleteMetric: (t: string, id: string) =>
    request<{ deleted: boolean }>(`/api/monitoring/metrics/${id}?tenant_id=${t}`, { method: "DELETE" }),
};

export { ApiError };

export interface UserRow {
  id: string;
  email: string;
  role: "owner" | "staff";
  created_at: string | null;
  must_change_password: boolean;
  is_me: boolean;
}

export const usersApi = {
  list: (t: string) => request<UserRow[]>(`/api/users/?tenant_id=${t}`),
  create: (t: string, email: string, role: "owner" | "staff") =>
    request<UserRow & { temporary_password: string }>("/api/users/", { method: "POST", ...json({ tenant_id: t, email, role }) }),
  setRole: (t: string, id: string, role: "owner" | "staff") =>
    request<UserRow>(`/api/users/${id}?tenant_id=${t}`, { method: "PATCH", ...json({ role }) }),
  resetPassword: (t: string, id: string) =>
    request<UserRow & { temporary_password: string }>(`/api/users/${id}/reset-password?tenant_id=${t}`, { method: "POST" }),
  remove: (t: string, id: string) => request<{ deleted: boolean }>(`/api/users/${id}?tenant_id=${t}`, { method: "DELETE" }),
  invite: (t: string, email: string, role: "owner" | "staff") =>
    request<UserRow>("/api/users/invite", { method: "POST", ...json({ tenant_id: t, email, role }) }),
  resendInvite: (t: string, id: string) =>
    request<{ ok: boolean }>(`/api/users/${id}/resend-invite?tenant_id=${t}`, { method: "POST" }),
};

// ---- public (signed-out) account recovery ----
async function publicPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw await parseError(res);
  return res.json();
}
export const forgotPassword = (email: string) => publicPost<{ ok: boolean; email_enabled: boolean; message: string }>("/api/auth/forgot-password", { email });
export const resetPassword = (token: string, newPassword: string) =>
  publicPost<{ ok: boolean }>("/api/auth/reset-password", { token, new_password: newPassword });
export const recoveryQuestions = (email: string) => publicPost<{ questions: string[] }>("/api/auth/recovery-questions", { email });
export const recoverWithAnswers = (email: string, answers: string[], newPassword: string) =>
  publicPost<{ ok: boolean }>("/api/auth/recover-with-answers", { email, answers, new_password: newPassword });

// ---- security questions (signed in) ----
export const securityQuestionsApi = {
  get: () => request<{ options: string[]; count: number; questions: string[] }>("/api/auth/security-questions"),
  save: (currentPassword: string, items: { question: string; answer: string }[]) =>
    request<{ questions: string[] }>("/api/auth/security-questions", { method: "PUT", ...json({ current_password: currentPassword, items }) }),
};

// ---- business picture ----
export interface Health {
  period_days: number;
  status: "healthy" | "watch" | "attention" | "critical" | string;
  score: number;
  headline: string;
  financials: { sales: number; expenses: number; profit: number; sales_change_pct: number | null; expenses_change_pct: number | null; profit_change_pct: number | null };
  open_alerts: Record<string, number>;
  top_alert: { id: string; title: string; severity: string } | null;
  stale_sources: string[];
  data_quality_score: number;
  score_basis: string;
}
export interface Quality {
  period_days: number; events: number; failed: number; failed_pct: number; score: number;
  sales_missing_pct: Record<string, number>;
  top_failure_reasons: { reason: string; count: number }[];
  by_connector: { connector: string; events: number; failed: number; failed_pct: number }[];
  notes: string[];
}
export interface Discovery {
  entities: { entity: string; events: number; first: string; last: string; span_days: number; has_amount_pct: number; has_quantity_pct: number; has_customer_pct: number; has_product_pct: number; has_location_pct: number }[];
  suggested_metrics: { entity: string; field: string; period_days: number; direction: "drop" | "rise"; already_monitored: boolean; enough_history: boolean; reason: string }[];
  next_steps: string[];
}
export interface EntityRow { id: string; entity_type: string; canonical_name: string; confidence: number }
export interface LinkRow { id: string | null; name: string; amount: number; events: number; share: number }
export interface Relationships {
  entity: { id: string; type: string; name: string }; period_days: number; measure: string; total: number; events: number;
  linked: Record<string, LinkRow[]>; statements: string[]; share_of_business: number | null;
}
export interface ContextItem { id: string; kind: string; name: string; starts_on: string; ends_on: string | null }
export interface FxRow { id: string; currency: string; rate_to_base: number; effective_date: string }

export interface TrendDay { date: string; sales: number; expenses: number }
export interface Trend { days: number; bucket: "day" | "week" | "month"; period: PeriodInfo; previous: TrendDay[]; current: TrendDay[] }
export interface AMetric { current: number; previous: number | null; change_pct: number | null }
export interface AMonth { month: string; sales: number; spending: number; profit: number; margin_pct: number | null; partial: boolean }
export interface ABranch { name: string; sales: number; spending: number; profit: number; margin_pct: number | null; share_pct: number | null; transactions: number; average_sale: number; sales_change_pct: number | null }
export interface ADimRow { name: string; amount: number; count: number; quantity: number; previous: number | null; change_pct: number | null; share_pct?: number | null; cumulative_pct?: number | null }
export interface ACustomer { name: string; amount: number; orders: number; share_pct: number | null; generic: boolean }
export interface AWeek { week_start: string; sales: number; spending: number }
export interface AProjection { week_start: string; expected: number; low: number; high: number }
export interface AInsight { tone: "good" | "warn" | "bad" | "info"; title: string; text: string }
export interface Analytics {
  period: PeriodInfo; days: number;
  headline: {
    sales: AMetric; spending: AMetric; profit: AMetric; average_sale: AMetric; transactions: AMetric; customers: AMetric;
    margin_pct: { current: number | null; previous: number | null; change_points: number | null };
    spending_ratio_pct: { current: number | null; previous: number | null };
  };
  monthly: AMonth[];
  branches: ABranch[];
  products: { top: ADimRow[]; count: number; others: { count: number; amount: number; share_pct: number | null }; products_for_80pct: number | null };
  spending: { categories: ADimRow[]; total: number };
  weekdays: { day: string; average_sales: number; days: number }[];
  customers: { top: ACustomer[]; named_count: number; new_customers: number | null; returning_customers: number | null; repeat_rate_pct: number | null;
               top_customer_share_pct: number | null; top5_share_pct: number | null; generic_share_pct: number | null };
  daily: { date: string; sales: number; spending: number }[];
  forecast: { weeks: AWeek[]; projection: AProjection[]; method: string; r_squared: number | null; reliable: boolean; note?: string;
              slope_per_week?: number; average_week?: number; weekly_noise_pct?: number | null };
  insights: AInsight[];
}
export interface SetupStatus { has_business: boolean; setup_key_configured: boolean }

export const setupApi = {
  status: async (): Promise<SetupStatus> => {
    const res = await fetch(`${API_BASE}/api/health/setup`);
    if (!res.ok) throw await parseError(res);
    return res.json();
  },
  /** First business + its owner. The setup key is the ADMIN_API_KEY from the host's environment settings. */
  createBusiness: async (setupKey: string, b: { name: string; owner_email: string; owner_password: string; timezone?: string; base_currency?: string }) => {
    const res = await fetch(`${API_BASE}/api/system/tenants`, { method: "POST", headers: { "Content-Type": "application/json", "x-admin-key": setupKey }, body: JSON.stringify(b) });
    if (!res.ok) throw await parseError(res);
    return res.json() as Promise<{ id: string; name: string }>;
  },
  /** Reset link for an account when the server cannot send email. Needs the same setup key. */
  resetLink: async (setupKey: string, email: string) => {
    const res = await fetch(`${API_BASE}/api/system/reset-link`, { method: "POST", headers: { "Content-Type": "application/json", "x-admin-key": setupKey }, body: JSON.stringify({ email }) });
    if (!res.ok) throw await parseError(res);
    return res.json() as Promise<{ link: string; expires_in_minutes: number }>;
  },
};

export type AiProvider = { id: string; label: string; kind: string; model: string; base_url: string | null; priority: number; is_active: boolean };
export const aiApi = {
  list: (t: string) => request<AiProvider[]>(`/api/ai/providers?tenant_id=${t}`),
  add: (t: string, b: { label: string; kind: string; api_key: string; model: string; base_url?: string }) =>
    request<AiProvider>(`/api/ai/providers?tenant_id=${t}`, { method: "POST", ...json(b) }),
  models: (t: string, b: { kind: string; api_key: string; base_url?: string }) =>
    request<{ models: string[] }>(`/api/ai/models?tenant_id=${t}`, { method: "POST", ...json(b) }),
  remove: (t: string, id: string) => requestVoid(`/api/ai/providers/${id}?tenant_id=${t}`, "DELETE"),
};

export const businessApi = {
  trend: (t: string, days = 30) => request<Trend>(`/api/business/trend?tenant_id=${t}&days=${days}`),
  analytics: (t: string, days = 90) => request<Analytics>(`/api/business/analytics?tenant_id=${t}&days=${days}`),
  analyticsPdf: (t: string, days = 90) => requestBlob(`/api/business/analytics.pdf?tenant_id=${t}&days=${days}`),
  dataCheck: (t: string, days = 30) => request<DataCheckResult>(`/api/business/data-check?tenant_id=${t}&period_days=${days}`),
  health: (t: string, days = 30) => request<Health>(`/api/business/health?tenant_id=${t}&days=${days}`),
  quality: (t: string, days = 30) => request<Quality>(`/api/business/quality?tenant_id=${t}&days=${days}`),
  discovery: (t: string) => request<Discovery>(`/api/business/discovery?tenant_id=${t}`),
  entities: (t: string) => request<EntityRow[]>(`/api/entities/?tenant_id=${t}`),
  relationships: (t: string, id: string, days = 90) => request<Relationships>(`/api/business/relationships/${id}?tenant_id=${t}&days=${days}`),
  listContext: (t: string) => request<ContextItem[]>(`/api/business/context?tenant_id=${t}`),
  addContext: (t: string, c: { kind: string; name: string; starts_on: string; ends_on: string | null }) =>
    request<ContextItem>(`/api/business/context?tenant_id=${t}`, { method: "POST", ...json(c) }),
  deleteContext: (t: string, id: string) => requestVoid(`/api/business/context/${id}?tenant_id=${t}`, "DELETE"),
  syncHolidays: (t: string) => request<{ added: number }>(`/api/business/context/sync-holidays?tenant_id=${t}`, { method: "POST" }),
  listFx: (t: string) => request<FxRow[]>(`/api/business/fx?tenant_id=${t}`),
  putFx: (t: string, f: { currency: string; rate_to_base: number; effective_date: string }) =>
    request<unknown>(`/api/business/fx?tenant_id=${t}`, { method: "PUT", ...json(f) }),
};

async function requestVoid(path: string, method: string): Promise<void> {
  const tokens = getTokens();
  const res = await fetch(`${API_BASE}${path}`, { method, headers: tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {} });
  if (!res.ok) throw await parseError(res);
}

// ---- tenant settings ----
export interface TenantSettings { id: string; name: string; timezone: string; base_currency: string; country: string | null; language: string; area: string | null }
export const tenantApi = {
  me: () => request<TenantSettings>("/api/tenants/me"),
  update: (patch: Partial<Omit<TenantSettings, "id">>) => request<TenantSettings>("/api/tenants/me", { method: "PATCH", ...json(patch) }),
};

// ---- data sources ----
export interface ConnectorRow {
  id: string; name: string; connector_type: string; is_active: boolean; last_sync_at: string | null; last_sync_status: string | null;
  expected_frequency: string | null; sync_interval_minutes: number | null; last_attempt_at: string | null;
  last_sync_error?: string | null; webhook_url?: string | null; webhook_token?: string | null;
}
/** What an import or sync reports back. Rows it could not read are skipped and named, never silently dropped. */
export interface RejectedRecord { when: string; amount: number | null; branch: string | null; product: string | null; entity: string; reasons: string[] }
export interface ImportResult {
  rows_fetched?: number; rows_processed?: number; new_rows?: number; updated_rows?: number; removed_rows?: number; duplicates?: number; failed?: number;
  skipped?: number; skipped_examples?: string[]; rejected_total?: number; rejected_examples?: RejectedRecord[];
  left_out?: number; left_out_total?: number; blank_rows?: number;       // money-out rows left out because the owner asked (not costs of the business)
}
const rejectedLine = (e: RejectedRecord) => `${e.when}${e.amount != null ? `, ${e.amount.toLocaleString()}` : ""}${e.branch ? `, ${e.branch}` : ""}: ${e.reasons.join(", ")}`;
/** What a sync or upload did, in plain words: what was added, edited, removed, and exactly which records were left out and why. */
export function importSummary(r: ImportResult, verb = "Checked"): string {
  const rows = r.rows_fetched ?? r.rows_processed;
  const changes = [`${r.new_rows ?? 0} added`, r.updated_rows ? `${r.updated_rows} updated` : "", r.removed_rows ? `${r.removed_rows} removed` : ""].filter(Boolean).join(", ");
  const head = r.new_rows != null ? `${verb}${rows != null ? ` ${rows.toLocaleString()} rows` : ""}: ${changes}, ${(r.duplicates ?? 0).toLocaleString()} unchanged.` : `Done: ${rows ?? 0} rows.`;
  const rejected = r.rejected_total ? ` ${r.rejected_total} record${r.rejected_total === 1 ? "" : "s"} left out of your totals (${(r.rejected_examples ?? []).slice(0, 2).map(rejectedLine).join("; ")}${r.rejected_total > 2 ? "; …" : ""}). See them under the source.` : "";
  return head + rejected + skippedNote(r) + leftOutNote(r) + blankNote(r);
}
export const blankNote = (r: ImportResult) => r.blank_rows ? ` ${r.blank_rows} empty row${r.blank_rows === 1 ? "" : "s"} ignored.` : "";
export const leftOutNote = (r: ImportResult) =>
  r.left_out ? ` ${r.left_out} money-out row${r.left_out === 1 ? "" : "s"} (${(r.left_out_total ?? 0).toLocaleString("en-US")} in all) left out of spending, as you asked.` : "";
export const skippedNote = (r: ImportResult) =>
  r.skipped ? ` ${r.skipped} row${r.skipped === 1 ? "" : "s"} skipped (${(r.skipped_examples ?? []).join("; ")}${r.skipped > (r.skipped_examples?.length ?? 0) ? "; …" : ""}).` : "";

/** Two sources that look like the same records (a POS and an M-Pesa statement), and confirmed merges. */
export interface OverlapSuggestion {
  entity: string; matched: number; share: number; window: string; window_days: number;
  keep: { id: string; name: string; records: number }; other: { id: string; name: string; records: number };
}
export interface OnlyHere { count: number; amount: number; recent: { timestamp: string; amount: number }[] }
export interface MergedPair {
  entity: string; counted_once: number; window_days: number;
  keep: { id: string; name: string; only_here: OnlyHere }; other: { id: string; name: string; only_here: OnlyHere };
}

/** What one source adds to the shared dataset. */
export interface Contribution { counted: number; matched: number; rejected: number }
export interface TestEventResult { would_create: { entity: string; entity_id: string | null; amount: number | null; currency: string | null; customer_id: string | null; time: string } }
export interface DbType { db_type: string; available: boolean; verified: boolean; default_port: number | null; notes: string }

export interface FileQuality {
  blank_rows: number; blank_columns: string[]; unnamed_columns: string[]; unreadable_dates: number; unreadable_amounts: number;
  rows_without_amount: number; kind: "transactions" | "partial" | "list";
}
export interface Inspection {
  columns: string[]; row_count: number; sample: Record<string, unknown>[]; suggested_mapping: Record<string, string>; source: "ai" | "rules";
  reasons: Record<string, string>; guessed: string[]; quality: FileQuality;     // what was found, what was ignored, what we guessed
}

// ---- sample businesses ----
export interface SampleKind { id: string; title: string; blurb: string }
export const demoApi = {
  list: (t: string) => request<{ samples: SampleKind[]; loaded: boolean }>(`/api/demo/samples?tenant_id=${t}`),
  load: (t: string, kind: string) => request<{ kind: string; events: number; alerts: number }>("/api/demo/samples", { method: "POST", body: JSON.stringify({ tenant_id: t, kind }) }),
  remove: (t: string) => request<{ removed: boolean }>(`/api/demo/samples?tenant_id=${t}`, { method: "DELETE" }),
};

export type SystemLink = {
  system: string; label: string; what: string; connected: boolean; download_url?: string | null; connector_id?: string;
  state?: string; headline?: string; detail?: string | null; notice?: string | null; is_active?: boolean; paused?: boolean; checking?: boolean;
  last_seen_at?: string | null; last_synced_at?: string | null; in_sync?: boolean; records?: number;
  status?: { path?: string; path_source?: string; searched?: string[]; candidates?: string[]; scopes?: { id: string; name: string }[]; scope_id?: string | null; scope_name?: string | null; schema_version?: string | null };
  last_pass?: { rows?: Record<string, number>; seconds?: number };
  verify?: { match?: boolean; differences?: unknown[] };
};

export const systemApi = {
  list: (t: string) => request<SystemLink[]>(`/api/agent/?tenant_id=${t}`),
  connect: (t: string, system: string) => request<{ connector_id: string; code: string }>("/api/agent/connect", { method: "POST", ...json({ tenant_id: t, system }) }),
  check: (t: string, id: string) => request<SystemLink>(`/api/agent/${id}/check?tenant_id=${t}`, { method: "POST" }),
  settings: (t: string, id: string, body: { path?: string; scope_id?: string; paused?: boolean }) => request<SystemLink>(`/api/agent/${id}/settings?tenant_id=${t}`, { method: "POST", ...json(body) }),
  confirm: (t: string, id: string) => request<SystemLink>(`/api/agent/${id}/confirm?tenant_id=${t}`, { method: "POST" }),
  resync: (t: string, id: string) => request<SystemLink>(`/api/agent/${id}/resync?tenant_id=${t}`, { method: "POST" }),
  disconnect: (t: string, id: string) => request<{ removed_records: number }>(`/api/agent/${id}?tenant_id=${t}`, { method: "DELETE" }),
};

export const connectorApi = {
  inspectUrl: (t: string, config: Record<string, unknown>) =>
    request<Inspection>("/api/connectors/inspect-url", { method: "POST", ...json({ tenant_id: t, config }) }),
  inspectFile: async (t: string, file: File): Promise<Inspection> => {
    const tokens = getTokens();
    const form = new FormData();
    form.append("tenant_id", t);
    form.append("file", file);
    const res = await fetch(`${API_BASE}/api/connectors/inspect-file`, { method: "POST", body: form, headers: tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {} });
    if (!res.ok) throw await parseError(res);
    return res.json();
  },
  list: (t: string) => request<ConnectorRow[]>(`/api/connectors/?tenant_id=${t}`),
  create: (body: { tenant_id: string; name: string; connector_type: string; config: Record<string, unknown>; expected_frequency?: string | null; sync_interval_minutes?: number | null }) =>
    request<ConnectorRow>("/api/connectors/", { method: "POST", ...json(body) }),
  update: (t: string, id: string, patch: { is_active?: boolean; sync_interval_minutes?: number | null; clear_sync_interval?: boolean }) =>
    request<ConnectorRow>(`/api/connectors/${id}?tenant_id=${t}`, { method: "PATCH", ...json(patch) }),
  contributions: (t: string) => request<Record<string, Contribution>>(`/api/connectors/contributions?tenant_id=${t}`),
  /** What a sample payment would become, using the source's own mapping. Stores nothing. */
  testEvent: (t: string, id: string, payload?: Record<string, unknown>) =>
    request<TestEventResult>(`/api/connectors/${id}/test-event?tenant_id=${t}`, { method: "POST", ...(payload ? json({ payload }) : {}) }),
  /** A live feed's link is shown once; a lost one is replaced, and the old one stops working. */
  newLink: (t: string, id: string) => request<{ webhook_url: string }>(`/api/connectors/${id}/new-link?tenant_id=${t}`, { method: "POST" }),
  overlaps: (t: string) => request<{ suggestions: OverlapSuggestion[]; merged: MergedPair[] }>(`/api/connectors/overlaps?tenant_id=${t}`),
  merge: (t: string, otherId: string, keepId: string) => request<{ counted_once: number }>(`/api/connectors/${otherId}/merge-into/${keepId}?tenant_id=${t}`, { method: "POST" }),
  unmerge: (t: string, otherId: string) => request<{ restored: number }>(`/api/connectors/${otherId}/merge?tenant_id=${t}`, { method: "DELETE" }),
  disconnect: (t: string, id: string) => request<{ removed_records: number }>(`/api/connectors/${id}?tenant_id=${t}`, { method: "DELETE" }),
  dbTypes: (t: string) => request<DbType[]>(`/api/connectors/meta/database-types?tenant_id=${t}`),
  sync: (t: string, id: string, type: string, confirmReplace = false) =>
    request<ImportResult>(`/api/connectors/${id}/sync-${type === "shared_folder" ? "folder" : type}?tenant_id=${t}${confirmReplace ? "&confirm_replace=true" : ""}`, { method: "POST" }),
  rejected: (t: string, id: string) => request<{ total: number; records: RejectedRecord[] }>(`/api/connectors/${id}/rejected?tenant_id=${t}`),
  /** `replace`: the file is the whole truth, so records this source holds that it does not have are removed. */
  upload: async (t: string, id: string, file: File, replace = false) => {
    const tokens = getTokens();
    const form = new FormData();
    form.append("tenant_id", t);
    form.append("file", file);
    if (replace) form.append("replace", "true");
    const res = await fetch(`${API_BASE}/api/connectors/${id}/upload`, { method: "POST", body: form, headers: tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {} });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as ImportResult;
  },
};

export interface CaptureEntry {
  entity: "sale" | "expense"; amount: number; timestamp?: string; quantity?: number; currency?: string;
  location?: string; customer?: string; product?: string; supplier?: string; note?: string; client_ref?: string;
}
export const captureApi = {
  submit: (t: string, entries: CaptureEntry[]) =>
    request<Record<string, unknown>>("/api/capture/", { method: "POST", ...json({ tenant_id: t, entries }) }),
};

// ---- accuracy, model, backtests ----
export interface ModelChange { id: string; kind: string; subject: string; description: string | null; effective_at: string; version: number; rebaseline: boolean; source: string }
export interface ModelOverview { version: number; changes: ModelChange[] }
export interface TruthRow { id: string; label: string; metric: string | null; starts_on: string; ends_on: string | null }
export interface BacktestResult {
  episodes: number; alerts_per_30_days: number; by_kind: Record<string, number>; precision: number | null; recall: number | null;
  labelled_events: number; note: string | null; threshold_multiplier: number;
}
export interface Episode { key: string; first_day: string; last_day: string; days_firing: number; kind: string; severity: string; title: string; metric: string | null }
export interface BacktestOutcome {
  range: { start: string; end: string; step_days: number }; tolerance_days: number;
  results: (BacktestResult & { sample_episodes?: Episode[] })[]; recommended_threshold_multiplier?: number | null; limitations?: string[];
}
export interface BacktestRun extends BacktestOutcome { id: string }
export interface BacktestListItem { id: string; created_at: string; params: Record<string, unknown>; result: BacktestOutcome }
export interface KindStat { alerts: number; closed: number; false_positive: number; dismissed: number; resolved: number; false_positive_rate: number | null; not_useful_rate: number | null; note: string | null }
export interface AccuracyReport {
  period_days: number; by_kind: Record<string, KindStat>; by_month: Record<string, KindStat>;
  calibration: { band: string; alerts: number; useful_rate: number | null }[]; live_recall: number | null; method: string;
}
export const accuracyApi = {
  model: (t: string) => request<ModelOverview>(`/api/accuracy/model?tenant_id=${t}`),
  declareChange: (t: string, c: { kind: string; subject: string; description?: string; effective_at: string; rebaseline: boolean }) =>
    request<{ id: string; version: number }>(`/api/accuracy/model/changes?tenant_id=${t}`, { method: "POST", ...json(c) }),
  clearRebaseline: (t: string, id: string) => requestVoid(`/api/accuracy/model/changes/${id}/clear-rebaseline?tenant_id=${t}`, "POST"),
  truth: (t: string) => request<TruthRow[]>(`/api/accuracy/ground-truth?tenant_id=${t}`),
  addTruth: (t: string, r: { label: string; metric?: string | null; starts_on: string; ends_on?: string | null }) =>
    request<{ id: string }>(`/api/accuracy/ground-truth?tenant_id=${t}`, { method: "POST", ...json(r) }),
  deleteTruth: (t: string, id: string) => requestVoid(`/api/accuracy/ground-truth/${id}?tenant_id=${t}`, "DELETE"),
  backtest: (t: string, b: { start: string; end: string; step_days: number; tolerance_days: number }) =>
    request<BacktestRun>(`/api/accuracy/backtest?tenant_id=${t}`, { method: "POST", ...json(b) }),
  backtests: (t: string) => request<BacktestListItem[]>(`/api/accuracy/backtest?tenant_id=${t}`),
  report: (t: string, days = 180) => request<AccuracyReport>(`/api/accuracy/report?tenant_id=${t}&days=${days}`),
};

// ---- licences (master account) ----
export interface MasterOverview {
  currency: string; as_of: string; paid_up: number; overdue: number; paused: number; monthly_recurring: number; collected_total: number; collected_this_month: number;
  licences: { issued: number; unused: number; active: number; revoked: number };
}
export interface MasterAccount {
  id: string; hint: string; label: string | null; status: "unused" | "active" | "revoked"; price: number; currency: string; business: string | null;
  owner_email: string | null; sources: number; activated_at: string | null; last_record_at: string | null; paid_through: string | null;
  total_paid: number; revoked_at: string | null; revoked_reason: string | null; payment_state: "free" | "paid" | "unpaid" | null;
  access: "unused" | "active" | "overdue" | "revoked";       // overdue: a paid licence past its grace period, paused until paid
}
export interface DemoBusiness { kind: string; business_name: string; email: string; password: string; tenant_id: string; licence_id: string; events: number; alerts: number }
export const masterApi = {
  whoami: () => request<{ master: boolean }>("/api/master/whoami").then(() => true).catch(() => false),
  overview: () => request<MasterOverview>("/api/master/overview"),
  accounts: () => request<MasterAccount[]>("/api/master/accounts"),
  issue: (count: number, label: string, price: number) => request<{ keys: { id: string; key: string; hint: string }[] }>("/api/master/licences", { method: "POST", body: JSON.stringify({ count, label, price }) }),
  revoke: (id: string, reason: string) => request<{ status: string }>(`/api/master/licences/${id}/revoke`, { method: "POST", body: JSON.stringify({ reason }) }),
  reinstate: (id: string) => request<{ status: string }>(`/api/master/licences/${id}/reinstate`, { method: "POST" }),
  demoBusinesses: () => request<{ businesses: DemoBusiness[] }>("/api/master/demo-businesses", { method: "POST", body: JSON.stringify({}) }),
  pay: (id: string, amount: number, months: number, reference: string) => request<{ paid_through: string }>(`/api/master/licences/${id}/payments`, { method: "POST", body: JSON.stringify({ amount, months, reference: reference || null }) }),
};
export async function activateLicence(body: { licence_key: string; business_name: string; email: string; password: string }): Promise<void> {
  const res = await fetch(`${API_BASE}/api/licences/activate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw await parseError(res);
  setTokens((await res.json()) as TokenPair);
}

// ---- live refresh ----
export const liveApi = {
  pulse: (t: string) => request<{ token: string; last_synced_at: string | null }>(`/api/live/pulse?tenant_id=${t}`),
};

// ---- targets ----
export type GoalStatus = "met" | "on_track" | "at_risk" | "off_track" | "not_started" | "no_data";
export interface GoalHistory { first_day: string; last_day: string; actual: number; met: boolean }
export interface GoalProgress {
  id: string; name: string; metric: "sales" | "spending" | "profit"; metric_label: string; cadence: "week" | "month" | "quarter" | "year";
  direction: "reach" | "within"; target: number; actual: number; pct_of_target: number | null; status: GoalStatus; projected: number | null;
  first_day: string; last_day: string; days_total: number; days_elapsed: number; days_remaining: number; data_through: string | null;
  pace_gap: number | null; per_day_needed: number | null; summary: string; history: GoalHistory[];
}
export interface GoalInput { name: string; metric: GoalProgress["metric"]; cadence: GoalProgress["cadence"]; target_amount: number }
export interface DriverRow { name: string; now: number; before: number; change: number; effect: number }
export interface GoalExplanation {
  progress: GoalProgress;
  drivers: { compared: { days: number; this: [string, string]; before: [string, string] } | null; sections: { title: string; hurt: DriverRow[]; helped: DriverRow[] }[] };
  ai: { explanation: string; next_step: string } | null; ai_error: string | null;
}
export interface ChangeLine { area: string; name: string; kind: "improved" | "worse"; now: number; before: number; change: number; change_pct: number | null; detail: string }
export interface Changes { period: PeriodInfo; days: number; improved: ChangeLine[]; worse: ChangeLine[]; note?: string }
export const goalsApi = {
  list: (t: string) => request<GoalProgress[]>(`/api/goals?tenant_id=${t}`),
  create: (t: string, g: GoalInput) => request<GoalProgress>("/api/goals", { method: "POST", ...json({ tenant_id: t, ...g }) }),
  update: (t: string, id: string, patch: Partial<Pick<GoalInput, "name" | "target_amount">>) => request<GoalProgress>(`/api/goals/${id}?tenant_id=${t}`, { method: "PATCH", ...json(patch) }),
  remove: (t: string, id: string) => requestVoid(`/api/goals/${id}?tenant_id=${t}`, "DELETE"),
  explain: (t: string, id: string) => request<GoalExplanation>(`/api/goals/${id}/explain?tenant_id=${t}`, { method: "POST" }),
  changes: (t: string, days = 30) => request<Changes>(`/api/goals/changes?tenant_id=${t}&days=${days}`),
};
