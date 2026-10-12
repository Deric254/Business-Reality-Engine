import { getTokens, setTokens, type TokenPair } from "./session";

// API base is configured at build/run time. No service API key lives in the
// browser bundle: users authenticate with their own per-tenant token.
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

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
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {}),
      ...options.headers,
    },
  });
  if (res.status === 401 && tokens && !retried) {
    if (await refreshTokens()) return request<T>(path, options, true);
    setTokens(null); // session is dead — listeners send the user to sign-in
  }
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function login(email: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await parseError(res);
  setTokens((await res.json()) as TokenPair);
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
}

export interface AlertDetail extends Alert {
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

export interface KpiBreakdown {
  period_days: number;
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
  suggestions?: string[];
}

export interface CoverageRow {
  area: string;
  status: "live" | "recent" | "stale" | "not_connected";
  last_data_at: string | null;
  checked_at: string;
}

export const api = {
  listTenants: () => request<Tenant[]>("/api/tenants/"),

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

export interface Channel {
  id: string;
  channel_type: "email" | "webhook";
  target: string;
  label: string | null;
  min_severity: string;
  enabled: boolean;
  signing_secret?: string | null;
}

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
  createChannel: (t: string, c: { channel_type: string; target: string; min_severity: string }) =>
    request<Channel>("/api/notifications/channels", { method: "POST", ...json({ tenant_id: t, ...c }) }),
  updateChannel: (t: string, id: string, patch: Partial<Pick<Channel, "enabled" | "min_severity">>) =>
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
};
