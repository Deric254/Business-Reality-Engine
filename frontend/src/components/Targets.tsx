import { useState } from "react";
import { STATUS } from "../lib/goalStatus";
import { goalsApi, type Changes, type ChangeLine, type GoalExplanation, type GoalProgress, type GoalStatus } from "../api/client";
import { Card, btnCls } from "./ui";
import { money } from "./ui";

const CADENCE: Record<string, string> = { week: "this week", month: "this month", quarter: "this quarter", year: "this year" };
const day = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-KE", { day: "numeric", month: "short" });

export function StatusPill({ status, within }: { status: GoalStatus; within?: boolean }) {
  const s = STATUS[status];
  const label = within && status === "off_track" ? "Over the limit" : s.label;
  return <span className="inline-flex items-center gap-1.5 text-xs font-semibold" style={{ color: s.text }}><span className="w-2 h-2 rounded-full" style={{ backgroundColor: s.color }} aria-hidden />{label}</span>;
}

/** Progress bar with a tick where an even pace would be today, so "ahead" and "behind" can be seen, not just read. */
export function ProgressBar({ g }: { g: GoalProgress }) {
  const pct = g.target ? Math.max(0, Math.min(100, (g.actual / g.target) * 100)) : 0;
  const pace = g.days_total ? (g.days_elapsed / g.days_total) * 100 : 0;
  return (
    <div className="relative h-2.5 rounded-full bg-panel-raised overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} aria-label={`${g.name}: ${Math.round(pct)}% of target`}>
      <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${pct}%`, backgroundColor: STATUS[g.status].color }} />
      {g.status !== "no_data" && g.status !== "not_started" && <div className="absolute inset-y-0 w-0.5 bg-ink-text/60" style={{ left: `${Math.min(100, pace)}%` }} title="Where an even pace would be today" />}
    </div>
  );
}

export function GoalSummary({ g, cur }: { g: GoalProgress; cur: string }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="font-semibold">{g.name} <span className="text-sm font-normal text-ink-muted">· {g.metric_label.toLowerCase()} {CADENCE[g.cadence]}</span></div>
        <StatusPill status={g.status} within={g.direction === "within"} />
      </div>
      <ProgressBar g={g} />
      <div className="flex flex-wrap justify-between gap-2 text-sm kpi-figure">
        <span><span className="font-semibold">{money(g.actual, cur)}</span> <span className="text-ink-muted">of {money(g.target, cur)}{g.direction === "within" ? " limit" : ""}</span></span>
        {g.projected != null && g.status !== "met" && <span className="text-ink-muted">on pace for {money(g.projected, cur)}</span>}
      </div>
    </div>
  );
}

function DriverList({ title, rows, cur }: { title: string; rows: { name: string; change: number; effect: number }[]; cur: string }) {
  if (!rows.length) return null;
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted mb-1">{title}</div>
      <ul className="space-y-0.5 text-sm">{rows.map((r) => <li key={r.name} className="flex justify-between gap-3"><span className="truncate">{r.name}</span><span className="kpi-figure shrink-0" style={{ color: r.effect > 0 ? "#146c3e" : "#b42318" }}>{r.change > 0 ? "+" : "−"}{money(Math.abs(r.change), cur)}</span></li>)}</ul>
    </div>
  );
}

/** Why: exact figures first (always), the AI's wording of them when a provider is connected. */
export function WhyPanel({ tenantId, goal, cur }: { tenantId: string; goal: GoalProgress; cur: string }) {
  const [res, setRes] = useState<GoalExplanation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = () => { setBusy(true); setErr(null); goalsApi.explain(tenantId, goal.id).then(setRes).catch((e) => setErr(e.message)).finally(() => setBusy(false)); };
  const cmp = res?.drivers.compared;
  return (
    <div className="space-y-3">
      <button className={btnCls} onClick={run} disabled={busy}>{busy ? "Working it out…" : res ? "Refresh the explanation" : "Why is this so?"}</button>
      {err && <p role="alert" className="text-sm text-critical">{err}</p>}
      {res && (
        <div className="space-y-4 border-t border-border pt-3" aria-live="polite">
          {res.ai ? (
            <div className="space-y-1.5">
              <p className="text-sm">{res.ai.explanation}</p>
              <p className="text-sm"><span className="font-semibold">Next step: </span>{res.ai.next_step}</p>
              <p className="text-xs text-ink-faint">Worded by AI from the exact figures below. It adds no numbers of its own.</p>
            </div>
          ) : <p className="text-sm text-ink-muted">{res.ai_error ?? res.progress.summary}</p>}
          {cmp && <p className="text-xs text-ink-muted">What moved, {day(cmp.this[0])} to {day(cmp.this[1])}, against {day(cmp.before[0])} to {day(cmp.before[1])}.</p>}
          {res.drivers.sections.length === 0 && cmp && <p className="text-sm text-ink-muted">Nothing moved enough by branch, product or category to point at.</p>}
          {res.drivers.sections.map((s) => (
            <div key={s.title} className="grid sm:grid-cols-2 gap-4">
              <DriverList title={`${s.title} hurting`} rows={s.hurt} cur={cur} />
              <DriverList title={`${s.title} helping`} rows={s.helped} cur={cur} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ChangeColumn({ title, lines, color, empty, cur }: { title: string; lines: ChangeLine[]; color: string; empty: string; cur: string }) {
  void cur;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm font-semibold"><span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} aria-hidden />{title}</div>
      {lines.length === 0 ? <p className="text-sm text-ink-muted">{empty}</p> : (
        <ul className="space-y-2">{lines.map((l) => (
          <li key={`${l.area}:${l.name}`} className="text-sm">
            <div><span className="font-medium">{l.name}</span> <span className="text-ink-faint">· {l.area.toLowerCase()}</span></div>
            <div className="text-ink-muted kpi-figure">{l.detail}</div>
          </li>
        ))}</ul>
      )}
    </div>
  );
}

/** What got better and what got worse since the period before, from the same complete-day windows as every other figure. */
export function ChangesCard({ changes, busy, label }: { changes: Changes | null; busy: boolean; label: string }) {
  if (!changes) return null;
  const nothing = !changes.improved.length && !changes.worse.length;
  return (
    <Card className={`space-y-4 transition-opacity ${busy ? "opacity-60" : ""}`}>
      <div>
        <h2 className="text-xl font-semibold tracking-tight">What changed</h2>
        <p className="text-sm text-ink-muted">{label}, against the period before it. Spending going up counts as worse.</p>
      </div>
      {changes.note ? <p className="text-sm text-ink-muted">{changes.note}</p> : nothing ? <p className="text-sm text-ink-muted">Nothing moved enough to report. Small ups and downs are left out on purpose.</p> : (
        <div className="grid sm:grid-cols-2 gap-6">
          <ChangeColumn title="Getting better" lines={changes.improved} color="#1f9254" empty="Nothing clearly improved." cur="" />
          <ChangeColumn title="Getting worse" lines={changes.worse} color="#d92d20" empty="Nothing clearly got worse." cur="" />
        </div>
      )}
    </Card>
  );
}
