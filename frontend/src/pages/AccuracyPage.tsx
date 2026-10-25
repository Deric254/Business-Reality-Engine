import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  ApiError, accuracyApi, type AccuracyReport, type BacktestOutcome, type KindStat, type ModelOverview, type TruthRow,
} from "../api/client";
import { getClaims } from "../api/session";
import { Shell } from "../components/Shell";
import { Bar, Card, Empty, Notice, Section, btnCls, inputCls, pct, primaryCls, today } from "../components/ui";
import { SEVERITY_COLOR } from "../lib/severity";
import { useT } from "../lib/i18n";
import { useLive } from "../lib/liveContext";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");
const CHANGE_KINDS = ["location_added", "location_discontinued", "product_added", "product_discontinued", "source_added", "source_replaced", "restructure"];
const label = (s: string) => s.replace(/_/g, " ");
const daysAgo = (n: number) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);

function ReportSection({ tenantId }: { tenantId: string }) {
  const t = useT();
  const [days, setDays] = useState(180);
  const [r, setR] = useState<AccuracyReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { tick } = useLive();            // alerts were raised or closed: the accuracy record follows
  useEffect(() => { setErr(null); accuracyApi.report(tenantId, days).then(setR).catch((e) => setErr(errText(e))); }, [tenantId, days, tick]);
  const kinds = r ? Object.entries(r.by_kind) : [];
  const row = (k: string, s: KindStat) => (
    <div key={k} className="space-y-1">
      <Bar label={`${label(k)} (${s.alerts} alerts, ${s.closed} reviewed)`} value={s.alerts} max={Math.max(...kinds.map(([, x]) => x.alerts), 1)} />
      <div className="text-xs text-ink-muted">
        {s.false_positive_rate == null ? s.note : `False alarms ${pct(s.false_positive_rate)} · not useful ${pct(s.not_useful_rate)}`}
      </div>
    </div>
  );
  return (
    <Section title={t("acc.report")} hint={r?.method}
      action={<select aria-label="Window" className={`${inputCls} !w-auto`} value={days} onChange={(e) => setDays(Number(e.target.value))}>{[30, 90, 180, 365].map((d) => <option key={d} value={d}>{d} days</option>)}</select>}>
      <Notice error={err} />
      {r && (kinds.length === 0 ? <Empty>No alerts in this window yet.</Empty> : (
        <div className="grid md:grid-cols-2 gap-3">
          <Card><div className="space-y-4">{kinds.map(([k, s]) => row(k, s))}</div></Card>
          <Card>
            <div className="text-xs text-ink-muted mb-2">Do higher-confidence alerts turn out more useful?</div>
            {r.calibration.length === 0 ? <p className="text-sm text-ink-muted">Needs more reviewed alerts before this means anything.</p> : (
              <div className="space-y-2">{r.calibration.map((c) => <Bar key={c.band} label={`Confidence ${c.band} (${c.alerts})`} value={(c.useful_rate ?? 0) * 100} max={100} text={pct(c.useful_rate)} color="#1f9254" />)}</div>
            )}
            {r.live_recall != null && <p className="text-xs text-ink-muted mt-3">Known incidents we caught: {pct(r.live_recall)}</p>}
          </Card>
        </div>
      ))}
    </Section>
  );
}

function BacktestSection({ tenantId, isOwner }: { tenantId: string; isOwner: boolean }) {
  const t = useT();
  const [start, setStart] = useState(daysAgo(90));
  const [end, setEnd] = useState(daysAgo(1));
  const [step, setStep] = useState(3);
  const [tol, setTol] = useState(3);
  const [run, setRun] = useState<BacktestOutcome | null>(null);
  const [past, setPast] = useState<{ id: string; created_at: string; result: BacktestOutcome }[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const loadPast = useCallback(() => accuracyApi.backtests(tenantId).then((p) => { setPast(p); setRun((cur) => cur ?? p[0]?.result ?? null); }).catch(() => undefined), [tenantId]);
  useEffect(() => { loadPast(); }, [loadPast]);
  async function go(e: FormEvent) {
    e.preventDefault(); setErr(null); setBusy(true);
    try { setRun(await accuracyApi.backtest(tenantId, { start, end, step_days: step, tolerance_days: tol })); loadPast(); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  const best = run?.recommended_threshold_multiplier;
  return (
    <Section title={t("acc.backtest")} hint="Replays the detectors over your own past data to show how many alerts they would have raised, and — if you have logged known incidents — how many they would have caught.">
      {isOwner && (
        <form onSubmit={go} className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-ink-muted">From<input type="date" required className={`${inputCls} mt-1`} value={start} max={end} onChange={(e) => setStart(e.target.value)} /></label>
          <label className="text-xs text-ink-muted">To<input type="date" required className={`${inputCls} mt-1`} value={end} min={start} max={today()} onChange={(e) => setEnd(e.target.value)} /></label>
          <label className="text-xs text-ink-muted">Step (days)<input type="number" min={1} max={30} className={`${inputCls} mt-1 !w-20`} value={step} onChange={(e) => setStep(Number(e.target.value))} /></label>
          <label className="text-xs text-ink-muted">Match within (days)<input type="number" min={0} max={30} className={`${inputCls} mt-1 !w-24`} value={tol} onChange={(e) => setTol(Number(e.target.value))} /></label>
          <button className={primaryCls} disabled={busy}>{busy ? "Replaying…" : "Run test"}</button>
        </form>
      )}
      <Notice error={err} />
      {run && (
        <Card>
          <div className="text-xs text-ink-muted mb-2">{run.range.start} → {run.range.end}, every {run.range.step_days} day(s)</div>
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead className="text-xs text-ink-muted"><tr className="text-left"><th className="p-2">Sensitivity</th><th className="p-2">Alerts / 30 days</th><th className="p-2">Precision</th><th className="p-2">Recall</th><th className="p-2">Mix</th></tr></thead>
            <tbody>{run.results.map((x) => (
              <tr key={x.threshold_multiplier} className={`border-t border-border ${best === x.threshold_multiplier ? "bg-panel-raised" : ""}`}>
                <td className="p-2 kpi-figure">×{x.threshold_multiplier}{best === x.threshold_multiplier && <span className="ml-2 text-xs text-opportunity">suggested</span>}</td>
                <td className="p-2 kpi-figure">{x.alerts_per_30_days}</td>
                <td className="p-2 kpi-figure">{pct(x.precision)}</td><td className="p-2 kpi-figure">{pct(x.recall)}</td>
                <td className="p-2 text-xs text-ink-muted">{Object.entries(x.by_kind).map(([k, n]) => `${k} ${n}`).join(", ") || "—"}</td>
              </tr>))}</tbody>
          </table></div>
          {run.results[0]?.note && <p className="text-xs text-ink-muted mt-2">{run.results[0].note}</p>}
          {run.results[0]?.sample_episodes && run.results[0].sample_episodes.length > 0 && (
            <details className="mt-3"><summary className="text-xs text-ink-muted cursor-pointer">Example episodes</summary>
              <ul className="mt-2 text-xs space-y-1">{run.results[0].sample_episodes.slice(0, 12).map((ep) => (
                <li key={`${ep.key}${ep.first_day}`}><span style={{ color: SEVERITY_COLOR[ep.severity] }}>{ep.severity}</span> {ep.title} <span className="text-ink-muted kpi-figure">{ep.first_day}{ep.last_day !== ep.first_day ? ` → ${ep.last_day}` : ""}</span></li>))}</ul></details>
          )}
          {run.limitations && <ul className="mt-3 text-xs text-ink-muted list-disc pl-4">{run.limitations.map((l) => <li key={l}>{l}</li>)}</ul>}
        </Card>
      )}
      {past.length > 1 && <p className="text-xs text-ink-muted">{past.length} earlier runs are kept on record.</p>}
    </Section>
  );
}

function TruthSection({ tenantId, isOwner }: { tenantId: string; isOwner: boolean }) {
  const t = useT();
  const [rows, setRows] = useState<TruthRow[]>([]);
  const [text, setText] = useState("");
  const [from, setFrom] = useState(today());
  const [to, setTo] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => accuracyApi.truth(tenantId).then(setRows).catch((e) => setErr(errText(e))), [tenantId]);
  useEffect(() => { load(); }, [load]);
  async function add(e: FormEvent) {
    e.preventDefault(); setErr(null);
    try { await accuracyApi.addTruth(tenantId, { label: text.trim(), starts_on: from, ends_on: to || null }); setText(""); setTo(""); await load(); }
    catch (x) { setErr(errText(x)); }
  }
  return (
    <Section title={t("acc.truth")} hint="Real incidents you know about (a stock-out, a till outage). They let a test measure whether the system would have noticed.">
      <Notice error={err} />
      {rows.length === 0 ? <Empty>{t("common.none")}</Empty> : (
        <ul className="divide-y divide-border border border-border rounded-sm">{rows.map((r) => (
          <li key={r.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
            <span>{r.label}<span className="text-ink-muted kpi-figure"> · {r.starts_on}{r.ends_on && r.ends_on !== r.starts_on ? ` → ${r.ends_on}` : ""}</span></span>
            {isOwner && <button className={btnCls} onClick={async () => { try { await accuracyApi.deleteTruth(tenantId, r.id); await load(); } catch (x) { setErr(errText(x)); } }}>{t("common.remove")}</button>}
          </li>))}</ul>
      )}
      {isOwner && (
        <form onSubmit={add} className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-ink-muted flex-1 min-w-[200px]">What happened<input required maxLength={200} className={`${inputCls} mt-1`} value={text} onChange={(e) => setText(e.target.value)} placeholder="Till system down at Maua" /></label>
          <label className="text-xs text-ink-muted">From<input type="date" required className={`${inputCls} mt-1`} value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label className="text-xs text-ink-muted">To (optional)<input type="date" min={from} className={`${inputCls} mt-1`} value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <button className={primaryCls}>{t("common.add")}</button>
        </form>
      )}
    </Section>
  );
}

function ChangesSection({ tenantId, isOwner }: { tenantId: string; isOwner: boolean }) {
  const t = useT();
  const [m, setM] = useState<ModelOverview | null>(null);
  const [kind, setKind] = useState(CHANGE_KINDS[0]);
  const [subject, setSubject] = useState("");
  const [desc, setDesc] = useState("");
  const [when, setWhen] = useState(today());
  const [rebase, setRebase] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => accuracyApi.model(tenantId).then(setM).catch((e) => setErr(errText(e))), [tenantId]);
  useEffect(() => { load(); }, [load]);
  async function add(e: FormEvent) {
    e.preventDefault(); setErr(null);
    try { await accuracyApi.declareChange(tenantId, { kind, subject: subject.trim(), description: desc.trim() || undefined, effective_at: `${when}T00:00:00`, rebaseline: rebase }); setSubject(""); setDesc(""); setRebase(false); await load(); }
    catch (x) { setErr(errText(x)); }
  }
  return (
    <Section title={t("acc.changes")} hint={`Opening a branch or switching your till system changes what "normal" is. Telling us keeps old alerts explainable. Business model version: ${m?.version ?? "…"}.`}>
      <Notice error={err} />
      {m && (m.changes.length === 0 ? <Empty>{t("common.none")}</Empty> : (
        <ul className="divide-y divide-border border border-border rounded-sm">{m.changes.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
            <span><span className="kpi-figure text-xs text-ink-muted mr-2">v{c.version}</span><strong>{label(c.kind)}</strong>: {c.subject}
              <span className="text-ink-muted kpi-figure"> · {c.effective_at.slice(0, 10)}</span>{c.source === "auto" && <span className="text-xs text-info ml-2">detected</span>}</span>
            {c.rebaseline && (isOwner
              ? <button className={btnCls} onClick={async () => { try { await accuracyApi.clearRebaseline(tenantId, c.id); await load(); } catch (x) { setErr(errText(x)); } }}>Re-learning — end now</button>
              : <span className="text-xs text-medium">Re-learning normal</span>)}
          </li>))}</ul>
      ))}
      {isOwner && (
        <form onSubmit={add} className="grid sm:grid-cols-2 gap-2 items-end">
          <label className="text-xs text-ink-muted">What changed<select className={`${inputCls} mt-1`} value={kind} onChange={(e) => setKind(e.target.value)}>{CHANGE_KINDS.map((k) => <option key={k} value={k}>{label(k)}</option>)}</select></label>
          <label className="text-xs text-ink-muted">Which one<input required maxLength={255} className={`${inputCls} mt-1`} value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Nkubu branch" /></label>
          <label className="text-xs text-ink-muted">When<input type="date" required max={today()} className={`${inputCls} mt-1`} value={when} onChange={(e) => setWhen(e.target.value)} /></label>
          <label className="text-xs text-ink-muted">Notes<input maxLength={1000} className={`${inputCls} mt-1`} value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
          <label className="text-xs text-ink-muted flex items-center gap-2 sm:col-span-2"><input type="checkbox" checked={rebase} onChange={(e) => setRebase(e.target.checked)} /> Numbers will look different from now on, so relearn what normal is.</label>
          <div><button className={primaryCls}>{t("common.add")}</button></div>
        </form>
      )}
    </Section>
  );
}

export function AccuracyPage() {
  const t = useT();
  const isOwner = getClaims().role === "owner";
  return (
    <Shell wide>
      {(tenant) => (
        <>
          <h1 className="display text-3xl sm:text-4xl">{t("acc.title")}</h1>
          <ReportSection tenantId={tenant.id} />
          <BacktestSection tenantId={tenant.id} isOwner={isOwner} />
          <TruthSection tenantId={tenant.id} isOwner={isOwner} />
          <ChangesSection tenantId={tenant.id} isOwner={isOwner} />
        </>
      )}
    </Shell>
  );
}
