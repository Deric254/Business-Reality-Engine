import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { goalsApi, tenantApi, type GoalInput, type GoalProgress, type Tenant } from "../api/client";
import { getClaims } from "../api/session";
import { Shell } from "../components/Shell";
import { GoalSummary, WhyPanel } from "../components/Targets";
import { STATUS } from "../lib/goalStatus";
import { Card, Empty, Notice, PageTitle, Skeleton, btnCls, inputCls, money, primaryCls } from "../components/ui";
import { useOnLiveChange } from "../lib/liveContext";

const METRICS: { v: GoalInput["metric"]; label: string; hint: string }[] = [
  { v: "sales", label: "Sales", hint: "Reach at least this much." },
  { v: "profit", label: "Profit", hint: "Sales minus recorded spending. Reach at least this much." },
  { v: "spending", label: "Spending", hint: "A limit: stay within this much." },
];
const CADENCES: { v: GoalInput["cadence"]; label: string }[] = [{ v: "week", label: "Each week" }, { v: "month", label: "Each month" }, { v: "quarter", label: "Each quarter" }, { v: "year", label: "Each year" }];
const rangeText = (g: GoalProgress) => `${new Date(g.first_day + "T00:00:00").toLocaleDateString("en-KE", { day: "numeric", month: "short" })} to ${new Date(g.last_day + "T00:00:00").toLocaleDateString("en-KE", { day: "numeric", month: "short", year: "numeric" })}`;

function NewGoal({ tenantId, onDone }: { tenantId: string; onDone: () => void }) {
  const [name, setName] = useState("");
  const [metric, setMetric] = useState<GoalInput["metric"]>("sales");
  const [cadence, setCadence] = useState<GoalInput["cadence"]>("month");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const value = Number(amount.replace(/,/g, ""));
  const valid = name.trim().length > 0 && Number.isFinite(value) && value > 0;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true); setErr(null);
    goalsApi.create(tenantId, { name: name.trim(), metric, cadence, target_amount: value })
      .then(() => { setName(""); setAmount(""); onDone(); })
      .catch((x) => setErr(x.message))
      .finally(() => setBusy(false));
  };
  return (
    <Card>
      <form onSubmit={submit} className="space-y-4" aria-label="Set a target">
        <h2 className="text-lg font-semibold">Set a target</h2>
        <div className="grid sm:grid-cols-2 gap-4">
          <label className="text-sm space-y-1 block"><span className="font-medium">Name</span><input className={inputCls} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Monthly sales" /></label>
          <label className="text-sm space-y-1 block"><span className="font-medium">Amount</span><input className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="2,000,000" /></label>
          <label className="text-sm space-y-1 block"><span className="font-medium">What to measure</span>
            <select className={inputCls} value={metric} onChange={(e) => setMetric(e.target.value as GoalInput["metric"])}>{METRICS.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}</select>
            <span className="text-xs text-ink-muted">{METRICS.find((m) => m.v === metric)?.hint}</span></label>
          <label className="text-sm space-y-1 block"><span className="font-medium">How often</span>
            <select className={inputCls} value={cadence} onChange={(e) => setCadence(e.target.value as GoalInput["cadence"])}>{CADENCES.map((c) => <option key={c.v} value={c.v}>{c.label}</option>)}</select>
            <span className="text-xs text-ink-muted">Counts calendar weeks (Monday to Sunday), months, quarters or years, on your business's clock.</span></label>
        </div>
        {err && <p role="alert" className="text-sm text-critical">{err}</p>}
        <button className={primaryCls} disabled={!valid || busy}>{busy ? "Saving…" : "Save target"}</button>
      </form>
    </Card>
  );
}

function GoalCard({ tenantId, g, cur, isOwner, onChanged }: { tenantId: string; g: GoalProgress; cur: string; isOwner: boolean; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [amount, setAmount] = useState(String(g.target));
  const [err, setErr] = useState<string | null>(null);
  const save = () => {
    const v = Number(amount.replace(/,/g, ""));
    if (!Number.isFinite(v) || v <= 0) { setErr("Enter an amount above zero."); return; }
    goalsApi.update(tenantId, g.id, { target_amount: v }).then(() => { setEditing(false); setErr(null); onChanged(); }).catch((e) => setErr(e.message));
  };
  const remove = () => { if (window.confirm(`Delete the target "${g.name}"?`)) goalsApi.remove(tenantId, g.id).then(onChanged).catch((e) => setErr(e.message)); };
  return (
    <Card accent={STATUS[g.status].color} className="space-y-4">
      <GoalSummary g={g} cur={cur} />
      <p className="text-sm text-ink-muted">{g.summary}</p>
      <p className="text-xs text-ink-faint">{rangeText(g)}{g.data_through ? `. Counts complete days up to ${new Date(g.data_through + "T00:00:00").toLocaleDateString("en-KE", { day: "numeric", month: "short" })}.` : "."}</p>
      {g.history.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted" aria-label="Previous periods">
          <span className="font-medium">Before:</span>
          {g.history.map((h) => <span key={h.first_day} className="kpi-figure" style={{ color: h.met ? "#146c3e" : "#b42318" }}>{new Date(h.first_day + "T00:00:00").toLocaleDateString("en-KE", { day: "numeric", month: "short" })}: {money(h.actual, cur)} {h.met ? "✓ met" : "✗ missed"}</span>)}
        </div>
      )}
      {g.status !== "no_data" && g.status !== "not_started" && <WhyPanel tenantId={tenantId} goal={g} cur={cur} />}
      {isOwner && (
        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
          {editing ? (
            <>
              <input aria-label="New target amount" className={`${inputCls} !w-40`} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
              <button className={btnCls} onClick={save}>Save</button>
              <button className={btnCls} onClick={() => { setEditing(false); setAmount(String(g.target)); setErr(null); }}>Cancel</button>
            </>
          ) : (
            <>
              <button className={btnCls} onClick={() => setEditing(true)}>Change amount</button>
              <button className="text-sm font-medium text-critical hover:underline" onClick={remove}>Delete</button>
            </>
          )}
          {err && <span role="alert" className="text-sm text-critical">{err}</span>}
        </div>
      )}
    </Card>
  );
}

function Body({ tenant }: { tenant: Tenant }) {
  const [goals, setGoals] = useState<GoalProgress[] | null>(null);
  const [cur, setCur] = useState("");
  const [error, setError] = useState<string | null>(null);
  const isOwner = getClaims().role === "owner";
  const latest = useRef(0);
  const load = useCallback(() => {
    const mine = ++latest.current;
    return Promise.all([goalsApi.list(tenant.id), tenantApi.me().then((m) => m.base_currency).catch(() => "")])
      .then(([g, c]) => { if (mine === latest.current) { setGoals(g); setCur(c); setError(null); } })
      .catch((e) => { if (mine === latest.current) setError(e.message); });
  }, [tenant.id]);
  useOnLiveChange(load);
  useEffect(() => { load(); }, [load]);
  const behind = (goals ?? []).filter((g) => g.status === "off_track" || g.status === "at_risk").length;
  return (
    <>
      <PageTitle title="Targets" sub={goals && goals.length ? (behind ? `${behind} of ${goals.length} need attention. Figures follow your data as it syncs.` : "Everything is on track or reached. Figures follow your data as it syncs.") : "Set what you are aiming for and see, as your data flows in, whether you are meeting it."} />
      {error && <Notice error={`Couldn't load your targets: ${error}`} />}
      {goals === null && !error && <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-40" /></div>}
      {goals !== null && goals.length === 0 && <Empty>No targets yet. {isOwner ? "Set one below." : "The owner of this business can set them."}</Empty>}
      {goals !== null && <div className="space-y-4">{goals.map((g) => <GoalCard key={g.id} tenantId={tenant.id} g={g} cur={cur} isOwner={isOwner} onChanged={load} />)}</div>}
      {isOwner && <NewGoal tenantId={tenant.id} onDone={load} />}
    </>
  );
}

export function GoalsPage() { return <Shell wide>{(tenant) => <Body tenant={tenant} />}</Shell>; }
