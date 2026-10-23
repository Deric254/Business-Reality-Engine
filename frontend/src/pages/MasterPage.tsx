import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { masterApi, type MasterAccount, type MasterOverview } from "../api/client";
import { Shell } from "../components/Shell";
import { Card, Notice, Skeleton, btnCls, inputCls, primaryCls } from "../components/ui";

const usd = (n: number) => `USD ${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const date = (iso: string | null) => (iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleDateString("en-KE", { day: "numeric", month: "short", year: "numeric" }) : "—");
const STATE: Record<string, string> = { paid: "Paid up", unpaid: "Not paid", free: "Free" };

function Stat({ label, value, busy }: { label: string; value: string; busy: boolean }) {
  return <div className="min-w-0"><div className="text-sm text-ink-muted">{label}</div>{busy ? <Skeleton className="h-9 w-28 mt-1.5" /> : <div className="figure text-3xl mt-0.5 kpi-figure">{value}</div>}</div>;
}

function Body() {
  const [ov, setOv] = useState<MasterOverview | null>(null);
  const [rows, setRows] = useState<MasterAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keys, setKeys] = useState<{ id: string; key: string }[] | null>(null);
  const [issue, setIssue] = useState({ count: "1", label: "", price: "1" });
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { Promise.all([masterApi.overview(), masterApi.accounts()]).then(([o, a]) => { setOv(o); setRows(a); }).catch((e) => setError(e.message)); }, []);
  useEffect(() => { load(); }, [load]);

  async function act(fn: () => Promise<unknown>) { setBusy(true); setError(null); try { await fn(); load(); } catch (e) { setError(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); } }
  const issueKeys = () => act(async () => { const r = await masterApi.issue(Math.max(1, Number(issue.count) || 1), issue.label.trim(), Number(issue.price) || 0); setKeys(r.keys); });
  const revoke = (a: MasterAccount) => { const why = window.prompt(`Revoke ${a.business ?? a.hint}? They lose access at once. Reason (optional):`); if (why !== null) void act(() => masterApi.revoke(a.id, why)); };
  const pay = (a: MasterAccount) => { const v = window.prompt(`Amount received from ${a.business} (USD), covering one month:`, String(a.price)); if (v === null) return; const ref = window.prompt("Payment reference (optional, for example the M-Pesa code):") ?? ""; void act(() => masterApi.pay(a.id, Number(v), 1, ref)); };
  const wait = ov == null;

  return (
    <>
      <h1 className="display text-3xl sm:text-4xl">Licences</h1>
      {error && <Notice error={error} />}
      <Card className="!p-5 sm:!p-7 space-y-6">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
          <Stat busy={wait} label="Monthly recurring" value={ov ? usd(ov.monthly_recurring) : ""} />
          <Stat busy={wait} label="Collected this month" value={ov ? usd(ov.collected_this_month) : ""} />
          <Stat busy={wait} label="Collected, all time" value={ov ? usd(ov.collected_total) : ""} />
          <Stat busy={wait} label="Paid up / not paid" value={ov ? `${ov.paid_up} / ${ov.overdue}` : ""} />
        </div>
        {ov && <p className="text-xs text-ink-muted">Money shown is only what you have recorded as received. Licences: {ov.licences.active} active, {ov.licences.unused} not yet activated, {ov.licences.revoked} revoked.</p>}
      </Card>

      <Card className="!p-5 sm:!p-7 space-y-4">
        <h2 className="text-xl font-semibold">Issue keys</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm font-medium">How many<input type="number" min={1} max={100} value={issue.count} onChange={(e) => setIssue({ ...issue, count: e.target.value })} className={`${inputCls} mt-1 w-24`} /></label>
          <label className="text-sm font-medium">Price per month (USD)<input type="number" min={0} step="0.01" value={issue.price} onChange={(e) => setIssue({ ...issue, price: e.target.value })} className={`${inputCls} mt-1 w-32`} /></label>
          <label className="text-sm font-medium flex-1 min-w-48">Issued to<input value={issue.label} placeholder="Name or business" onChange={(e) => setIssue({ ...issue, label: e.target.value })} className={`${inputCls} mt-1`} /></label>
          <button className={primaryCls} disabled={busy} onClick={issueKeys}>Issue</button>
        </div>
        {keys && (
          <div className="border border-border rounded-sm p-3 space-y-2" role="status">
            <p className="text-sm font-semibold">Copy these now. They are shown once and cannot be recovered.</p>
            <pre className="kpi-figure text-sm whitespace-pre-wrap break-all select-all">{keys.map((k) => k.key).join("\n")}</pre>
            <div className="flex gap-3"><button className={btnCls} onClick={() => navigator.clipboard?.writeText(keys.map((k) => k.key).join("\n"))}>Copy</button><button className={btnCls} onClick={() => setKeys(null)}>I have saved them</button></div>
          </div>
        )}
      </Card>

      <Card className="!p-0 overflow-x-auto">
        {rows == null ? <div className="p-5"><Skeleton className="h-24 w-full" /></div> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-ink-muted border-b border-border">{["Business", "Key", "Status", "Paid through", "Total paid", "Last record", ""].map((h) => <th key={h} className="px-4 py-3 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">
              {rows.length === 0 && <tr><td colSpan={7} className="px-4 py-6 text-ink-muted">No licences yet.</td></tr>}
              {rows.map((a) => (
                <tr key={a.id}>
                  <td className="px-4 py-3"><div className="font-medium">{a.business ?? a.label ?? "Not activated"}</div><div className="text-xs text-ink-muted">{a.owner_email ?? "Waiting for activation"}</div></td>
                  <td className="px-4 py-3 kpi-figure text-xs">{a.hint}</td>
                  <td className="px-4 py-3">{a.status === "active" ? (a.payment_state ? STATE[a.payment_state] : "Active") : a.status === "revoked" ? <span title={a.revoked_reason ?? ""}>Revoked</span> : "Not activated"}</td>
                  <td className="px-4 py-3 kpi-figure">{date(a.paid_through)}</td>
                  <td className="px-4 py-3 kpi-figure">{usd(a.total_paid)}</td>
                  <td className="px-4 py-3 kpi-figure">{date(a.last_record_at)}</td>
                  <td className="px-4 py-3 whitespace-nowrap text-right space-x-2">
                    {a.status === "active" && a.price > 0 && <button className={btnCls} disabled={busy} onClick={() => pay(a)}>Record payment</button>}
                    {a.status !== "revoked" && a.label !== "Platform owner" && <button className={btnCls} disabled={busy} onClick={() => revoke(a)}>Revoke</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

export function MasterPage() {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => { masterApi.whoami().then(setOk); }, []);
  if (ok === false) return <Navigate to="/" replace />;
  return <Shell wide>{() => (ok ? <Body /> : <Skeleton className="h-40 w-full" />)}</Shell>;
}
