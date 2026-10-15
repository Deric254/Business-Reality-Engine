import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Shell } from "../components/Shell";
import { PageTitle } from "../components/ui";
import { api, settingsApi, usersApi, ApiError, type Channel, type Metric, type Tenant, type UserRow } from "../api/client";
import { PasswordForm } from "../components/PasswordForm";
import { SecurityQuestionsForm } from "../components/SecurityQuestionsForm";
import { getClaims } from "../api/session";
import { AiProviders } from "../components/AiProviders";

const SEVERITIES = ["critical", "high", "medium", "informational"];
const TARGET_LABEL: Record<string, string> = {
  email: "Email address", webhook: "Webhook URL (https)", slack: "Slack incoming-webhook URL", teams: "Teams workflow webhook URL",
  sms: "Phone number (international format)", whatsapp: "WhatsApp number (international format)",
};
const KINDS = ["deviation", "opportunity", "trend", "daily", "silence", "threshold", "forecast", "risk", "unknown", "quality"];
const DIGEST_OK = ["email", "slack", "teams"];

/** Quiet hours, daily digest and filters for one channel. Times are UTC. */
function ChannelOptions({ c, onSave }: { c: Channel; onSave: (patch: Parameters<typeof settingsApi.updateChannel>[2]) => void }) {
  const [qs, setQs] = useState(c.quiet_start ?? "");
  const [qe, setQe] = useState(c.quiet_end ?? "");
  const [mode, setMode] = useState(c.digest_mode);
  const [hour, setHour] = useState(c.digest_hour);
  const [kinds, setKinds] = useState<string[]>(c.filter_kinds ?? []);
  const [ents, setEnts] = useState((c.filter_entities ?? []).join(", "));
  const toggle = (k: string) => setKinds((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]));
  const f = "bg-panel border border-field rounded-sm px-2 py-1 text-sm text-ink-text";
  return (
    <details className="basis-full text-sm">
      <summary className="cursor-pointer text-xs text-ink-muted">Delivery options</summary>
      <div className="mt-3 grid sm:grid-cols-2 gap-4">
        <fieldset><legend className="text-xs text-ink-muted mb-1">Quiet hours (UTC) — critical alerts still come through</legend>
          <input type="time" aria-label="Quiet from" className={f} value={qs} onChange={(e) => setQs(e.target.value)} />
          <span className="mx-2 text-ink-muted">to</span>
          <input type="time" aria-label="Quiet until" className={f} value={qe} onChange={(e) => setQe(e.target.value)} />
        </fieldset>
        {DIGEST_OK.includes(c.channel_type) && (
          <fieldset><legend className="text-xs text-ink-muted mb-1">Medium / informational / opportunity alerts</legend>
            <select aria-label="Digest mode" className={f} value={mode} onChange={(e) => setMode(e.target.value as "off" | "daily")}>
              <option value="off">Send as they happen</option><option value="daily">One daily digest</option></select>
            {mode === "daily" && <><span className="mx-2 text-ink-muted">at</span>
              <select aria-label="Digest hour (UTC)" className={f} value={hour} onChange={(e) => setHour(Number(e.target.value))}>
                {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00 UTC</option>)}</select></>}
          </fieldset>
        )}
        <fieldset className="sm:col-span-2"><legend className="text-xs text-ink-muted mb-1">Only these kinds of alert (none ticked = all)</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">{KINDS.map((k) => (
            <label key={k} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={kinds.includes(k)} onChange={() => toggle(k)} />{k}</label>))}</div>
        </fieldset>
        <label className="sm:col-span-2 text-xs text-ink-muted">Only these record types, comma separated (blank = all)
          <input className={`${f} block mt-1 w-full`} value={ents} onChange={(e) => setEnts(e.target.value)} placeholder="sale, expense" /></label>
      </div>
      <button className="mt-3 bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-1 text-xs" onClick={() => {
        const entities = ents.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
        const quiet = qs && qe ? { quiet_start: qs, quiet_end: qe } : { clear_quiet_hours: true };
        onSave({ ...quiet, digest_mode: mode, digest_hour: hour, filter_kinds: kinds.length ? kinds : null, filter_entities: entities.length ? entities : null,
          ...(!kinds.length && !entities.length ? { clear_filters: true } : {}) });
      }}>Save options</button>
    </details>
  );
}

function errText(e: unknown) {
  return e instanceof ApiError ? e.message : "Something went wrong. Try again.";
}

function SettingsBody() {
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [temp, setTemp] = useState<{ email: string; password: string } | null>(null);
  const [uEmail, setUEmail] = useState("");
  const [uRole, setURole] = useState<"owner" | "staff">("staff");
  const isOwner = getClaims().role === "owner";

  const [chType, setChType] = useState("email");
  const [chTarget, setChTarget] = useState("");
  const [chSeverity, setChSeverity] = useState("high");
  const [mEntity, setMEntity] = useState("sale");
  const [mField, setMField] = useState("amount");
  const [mDays, setMDays] = useState(7);
  const [mThreshold, setMThreshold] = useState(10);
  const [mDirection, setMDirection] = useState<"drop" | "rise">("drop");
  const [mLimit, setMLimit] = useState("");

  const load = useCallback(async (t: Tenant) => {
    try {
      const [c, m] = await Promise.all([settingsApi.listChannels(t.id), settingsApi.listMetrics(t.id)]);
      setChannels(c);
      setMetrics(m);
      if (isOwner) setUsers(await usersApi.list(t.id));
    } catch (e) {
      setError(errText(e));
    }
  }, [isOwner]);

  useEffect(() => {
    api.listTenants().then((ts) => {
      if (ts[0]) {
        setTenant(ts[0]);
        load(ts[0]);
      }
    }).catch((e) => setError(errText(e)));
  }, [load]);

  useEffect(() => { if (tenant && window.location.hash === "#sq") document.getElementById("sq")?.scrollIntoView(); }, [tenant]);

  async function run(action: () => Promise<unknown>, done?: string) {
    if (!tenant) return;
    setError(null);
    setNotice(null);
    try {
      await action();
      if (done) setNotice(done);
      await load(tenant);
    } catch (e) {
      setError(errText(e));
    }
  }

  async function addChannel(e: FormEvent) {
    e.preventDefault();
    if (!tenant) return;
    await run(async () => {
      const created = await settingsApi.createChannel(tenant.id, { channel_type: chType, target: chTarget.trim(), min_severity: chSeverity });
      setChTarget("");
      setSecret(created.signing_secret ?? null);
    }, "Channel added.");
  }

  async function test(c: Channel) {
    if (!tenant) return;
    setError(null);
    setNotice(null);
    try {
      const r = await settingsApi.testChannel(tenant.id, c.id);
      if (r.ok) setNotice(`Test sent to ${c.target}.`);
      else setError(`Test failed: ${r.error ?? "unknown error"}`);
    } catch (e) {
      setError(errText(e));
    }
  }

  async function addUser(e: FormEvent) {
    e.preventDefault();
    if (!tenant) return;
    await run(async () => {
      const u = await usersApi.create(tenant.id, uEmail.trim(), uRole);
      setUEmail("");
      setTemp({ email: u.email, password: u.temporary_password });
    }, "Account created.");
  }

  async function inviteUser() {
    if (!tenant || !uEmail.trim()) return;
    await run(async () => { await usersApi.invite(tenant.id, uEmail.trim(), uRole); setUEmail(""); }, "Invitation emailed. The link works for 7 days.");
  }

  async function resetUser(u: UserRow) {
    if (!tenant || !window.confirm(`Reset the password for ${u.email}? They will be signed out everywhere.`)) return;
    await run(async () => {
      const r = await usersApi.resetPassword(tenant.id, u.id);
      setTemp({ email: r.email, password: r.temporary_password });
    });
  }

  const field = "bg-panel border border-field rounded-sm px-2 py-1.5 text-sm text-ink-text";
  const btn = "text-xs border border-border rounded-sm px-2 py-1 text-ink-muted hover:text-ink-text";

  return (
    <div className="space-y-10 max-w-3xl">
      <PageTitle title="Settings" sub="Where alerts go, who can sign in, and what is watched." />
      <div className="space-y-10">
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        {notice && <p role="status" className="text-sm text-opportunity">{notice}</p>}

        {tenant && (
          <section aria-labelledby="ai">
            <h2 id="ai" className="text-base font-semibold mb-1">AI assistant</h2>
            <p className="text-sm text-ink-muted mb-4">Powers the Ask box and the plain-language explanations on alerts. Add one or more keys; you can use free providers.</p>
            <AiProviders tenantId={tenant.id} isOwner={isOwner} />
          </section>
        )}

        <section aria-labelledby="ch">
          <h2 id="ch" className="text-base font-semibold mb-1">Where alerts are sent</h2>
          <p className="text-sm text-ink-muted mb-4">Each channel receives alerts at or above its severity. Only owners can change channels.</p>
          {secret && (
            <div className="mb-4 p-3 bg-panel border border-border rounded-sm text-sm">
              <p className="mb-1">Signing secret for the new webhook. Copy it now; it is shown only once.</p>
              <code className="kpi-figure break-all select-all">{secret}</code>
              <button className={`${btn} ml-3`} onClick={() => setSecret(null)}>Done</button>
            </div>
          )}
          {channels.length === 0 ? (
            <p className="text-sm text-ink-muted mb-4">No channels yet. Alerts only appear in this dashboard until you add one.</p>
          ) : (
            <ul className="divide-y divide-border border border-border rounded-sm mb-4">
              {channels.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                  <span className="text-ink-muted w-16">{c.channel_type}</span>
                  <span className="flex-1 break-all">{c.target}</span>
                  <select
                    aria-label={`Minimum severity for ${c.target}`} value={c.min_severity} className={field}
                    onChange={(e) => run(() => settingsApi.updateChannel(tenant!.id, c.id, { min_severity: e.target.value }))}
                  >
                    {SEVERITIES.map((s) => <option key={s} value={s}>{s}+</option>)}
                  </select>
                  <label className="flex items-center gap-1 text-ink-muted">
                    <input type="checkbox" checked={c.enabled} onChange={(e) => run(() => settingsApi.updateChannel(tenant!.id, c.id, { enabled: e.target.checked }))} />
                    On
                  </label>
                  <button className={btn} onClick={() => test(c)}>Send test</button>
                  <button className={btn} onClick={() => run(() => settingsApi.deleteChannel(tenant!.id, c.id), "Channel removed.")}>Remove</button>
                  <ChannelOptions key={`${c.id}${c.quiet_start}${c.quiet_end}${c.digest_mode}${c.digest_hour}${(c.filter_kinds ?? []).join()}${(c.filter_entities ?? []).join()}`} c={c}
                    onSave={(patch) => run(() => settingsApi.updateChannel(tenant!.id, c.id, patch), "Options saved.")} />
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={addChannel} className="flex flex-wrap items-end gap-3">
            <label className="text-sm text-ink-muted">Type
              <select className={`${field} block mt-1`} value={chType} onChange={(e) => setChType(e.target.value)}>
                <option value="email">Email</option>
                <option value="slack">Slack</option>
                <option value="teams">Microsoft Teams</option>
                <option value="sms">SMS</option>
                <option value="whatsapp">WhatsApp</option>
                <option value="webhook">Custom webhook</option>
              </select>
            </label>
            <label className="text-sm text-ink-muted flex-1 min-w-48">{TARGET_LABEL[chType]}
              <input required className={`${field} block mt-1 w-full`} value={chTarget} onChange={(e) => setChTarget(e.target.value)}
                type={chType === "email" ? "email" : chType === "sms" || chType === "whatsapp" ? "tel" : "url"}
                placeholder={chType === "sms" || chType === "whatsapp" ? "+254712345678" : undefined} />
            </label>
            <label className="text-sm text-ink-muted">Send at
              <select className={`${field} block mt-1`} value={chSeverity} onChange={(e) => setChSeverity(e.target.value)}>
                {SEVERITIES.map((s) => <option key={s} value={s}>{s} and above</option>)}
              </select>
            </label>
            <button type="submit" className="bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-1.5 text-sm">Add channel</button>
          </form>
        </section>

        {isOwner && (
          <section aria-labelledby="us">
            <h2 id="us" className="text-base font-semibold mb-1">People</h2>
            <p className="text-sm text-ink-muted mb-4">Owners manage settings, channels and people. Staff can view everything else. A new person gets a one-time temporary password and must choose their own at first sign-in.</p>
            {temp && (
              <div role="status" className="border border-border bg-panel rounded-sm p-3 mb-4 text-sm">
                <p className="mb-1">Temporary password for <strong>{temp.email}</strong>. Share it privately; it is shown only once.</p>
                <code className="kpi-figure break-all select-all">{temp.password}</code>
                <button className={`${btn} ml-3`} onClick={() => setTemp(null)}>Done</button>
              </div>
            )}
            <ul className="divide-y divide-border border border-border rounded-sm mb-4">
              {users.map((u) => (
                <li key={u.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                  <span className="flex-1 break-all">{u.email}{u.is_me ? " (you)" : ""}{u.must_change_password ? " · hasn't signed in yet" : ""}</span>
                  {u.must_change_password && !u.is_me && <button className={btn} onClick={() => run(() => usersApi.resendInvite(tenant!.id, u.id), "Invitation re-sent.")}>Resend invite</button>}
                  <select
                    aria-label={`Role for ${u.email}`} value={u.role} disabled={u.is_me} className={field}
                    onChange={(e) => run(() => usersApi.setRole(tenant!.id, u.id, e.target.value as "owner" | "staff"), "Role updated.")}
                  >
                    <option value="staff">staff</option>
                    <option value="owner">owner</option>
                  </select>
                  {!u.is_me && <button className={btn} onClick={() => resetUser(u)}>Reset password</button>}
                  {!u.is_me && (
                    <button className={btn} onClick={() => { if (window.confirm(`Remove ${u.email}? They are signed out immediately.`)) run(() => usersApi.remove(tenant!.id, u.id), "Person removed."); }}>Remove</button>
                  )}
                </li>
              ))}
            </ul>
            <form onSubmit={addUser} className="flex flex-wrap items-end gap-3">
              <label className="text-sm text-ink-muted">Email
                <input type="email" required value={uEmail} onChange={(e) => setUEmail(e.target.value)} className={`${field} block mt-1 w-64`} />
              </label>
              <label className="text-sm text-ink-muted">Role
                <select value={uRole} onChange={(e) => setURole(e.target.value as "owner" | "staff")} className={`${field} block mt-1`}>
                  <option value="staff">staff</option>
                  <option value="owner">owner</option>
                </select>
              </label>
              <button type="button" onClick={inviteUser} className="bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-1.5 text-sm">Invite by email</button>
              <button type="submit" className={btn}>Create with temporary password</button>
            </form>
          </section>
        )}

        <section aria-labelledby="pw">
          <h2 id="pw" className="text-base font-semibold mb-1">Your password</h2>
          <p className="text-sm text-ink-muted mb-4">Changing it signs you out of every other device.</p>
          <PasswordForm />
        </section>

        <section aria-labelledby="sq">
          <h2 id="sq" className="text-base font-semibold mb-1">Security questions</h2>
          <p className="text-sm text-ink-muted mb-4">A way back into your account that works without email.</p>
          <SecurityQuestionsForm />
        </section>

        <section aria-labelledby="mt">
          <h2 id="mt" className="text-base font-semibold mb-1">What is watched</h2>
          <p className="text-sm text-ink-muted mb-4">An alert is raised when the latest period moves the bad way against the average of the previous four by more than the threshold. Trends, unusual days, and silent data sources are also watched for every metric. Once you add a metric, only the metrics listed here are watched.</p>
          {metrics.length === 0 ? (
            <p className="text-sm text-ink-muted mb-4">Nothing configured, so sales and expenses are watched weekly with a 10% threshold.</p>
          ) : (
            <ul className="divide-y divide-border border border-border rounded-sm mb-4">
              {metrics.map((m) => (
                <li key={m.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                  <span className="flex-1">{m.entity} {m.field}, every {m.period_days} days, alert on a {Number(m.threshold_pct)}% {m.direction === "rise" ? "increase" : "drop"}{m.absolute_threshold != null ? `, or a total ${m.direction === "rise" ? "above" : "below"} ${Number(m.absolute_threshold).toLocaleString()}` : ""}</span>
                  <label className="flex items-center gap-1 text-ink-muted">
                    <input type="checkbox" checked={m.enabled} onChange={(e) => run(() => settingsApi.updateMetric(tenant!.id, m.id, { enabled: e.target.checked }))} />
                    On
                  </label>
                  <button className={btn} onClick={() => run(() => settingsApi.deleteMetric(tenant!.id, m.id), "Metric removed.")}>Remove</button>
                </li>
              ))}
            </ul>
          )}
          <form
            onSubmit={(e) => { e.preventDefault(); run(() => settingsApi.createMetric(tenant!.id, { entity: mEntity.trim().toLowerCase(), field: mField, period_days: mDays, threshold_pct: mThreshold, direction: mDirection, absolute_threshold: mLimit.trim() === "" ? null : Number(mLimit) }), "Metric added."); }}
            className="flex flex-wrap items-end gap-3"
          >
            <label className="text-sm text-ink-muted">Record type
              <input required pattern="[a-z][a-z0-9_]{0,39}" className={`${field} block mt-1 w-28`} value={mEntity} onChange={(e) => setMEntity(e.target.value)} />
            </label>
            <label className="text-sm text-ink-muted">Measure
              <select className={`${field} block mt-1`} value={mField} onChange={(e) => setMField(e.target.value)}>
                <option value="amount">Amount</option>
                <option value="quantity">Quantity</option>
              </select>
            </label>
            <label className="text-sm text-ink-muted">Period (days)
              <input type="number" min={1} max={90} required className={`${field} block mt-1 w-24`} value={mDays} onChange={(e) => setMDays(Number(e.target.value))} />
            </label>
            <label className="text-sm text-ink-muted">Bad when it
              <select className={`${field} block mt-1`} value={mDirection} onChange={(e) => setMDirection(e.target.value as "drop" | "rise")}>
                <option value="drop">Falls (sales, income)</option>
                <option value="rise">Rises (expenses, costs)</option>
              </select>
            </label>
            <label className="text-sm text-ink-muted">Change threshold (%)
              <input type="number" min={1} max={100} required className={`${field} block mt-1 w-24`} value={mThreshold} onChange={(e) => setMThreshold(Number(e.target.value))} />
            </label>
            <label className="text-sm text-ink-muted">Fixed limit (optional)
              <input type="number" min={0} step="any" className={`${field} block mt-1 w-32`} value={mLimit} onChange={(e) => setMLimit(e.target.value)} />
            </label>
            <button type="submit" className="bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-1.5 text-sm">Add metric</button>
          </form>
        </section>
      </div>
    </div>
  );
}

export function SettingsPage() {
  return <Shell>{() => <SettingsBody />}</Shell>;
}
