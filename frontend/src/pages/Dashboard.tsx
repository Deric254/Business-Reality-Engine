import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, businessApi, connectorApi, type Alert, type ConnectorRow, type Health, type KpiBreakdown, type Trend } from "../api/client";
import { getClaims } from "../api/session";
import { Shell } from "../components/Shell";
import { AskBox } from "../components/AskBox";
import { AlertRow } from "../components/AlertRow";
import { TrendChart } from "../components/TrendChart";
import { Card, Empty, Notice, Skeleton, btnCls, money, primaryCls } from "../components/ui";
import { useT } from "../lib/i18n";
import type { Tenant } from "../api/client";

const CLOSED = new Set(["resolved", "dismissed", "false_positive"]);

/** Today usually has no (or partial) data yet; an empty last day would draw a false crash to zero, so leave it out. */
function chartDays(t: Trend): Trend {
  const last = t.current[t.current.length - 1];
  if (last && last.sales === 0 && last.expenses === 0) {
    const n = t.current.length - 1;
    return { ...t, current: t.current.slice(0, n), previous: t.previous.slice(0, n) };
  }
  return t;
}

function change(cur: number, prev: number): number | null {
  return prev === 0 ? null : ((cur - prev) / prev) * 100;
}

function Kpi({ label, value, delta, goodWhen }: { label: string; value: number; delta: number | null; goodWhen: "up" | "down" }) {
  const good = delta == null || Math.abs(delta) < 0.5 ? null : (delta > 0) === (goodWhen === "up");
  return (
    <div className="min-w-0">
      <div className="text-sm text-ink-muted">{label}</div>
      <div className="figure text-3xl sm:text-4xl mt-0.5 break-words">{money(value)}</div>
      <div className="text-sm mt-1 kpi-figure" style={{ color: good == null ? "var(--neutral)" : good ? "var(--good)" : "var(--bad)" }}>
        {delta == null ? "No earlier period to compare" : Math.abs(delta) < 0.5 ? "About the same as before" : `${delta > 0 ? "Up" : "Down"} ${Math.abs(delta).toFixed(0)}% on the period before`}
      </div>
    </div>
  );
}

function SourcesStrip({ rows }: { rows: ConnectorRow[] }) {
  const bad = rows.filter((c) => c.is_active && (c.last_sync_status === "failed" || c.last_sync_status === "error"));
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div className="font-semibold">{rows.length} data source{rows.length === 1 ? "" : "s"} connected</div>
        <div className="text-sm text-ink-muted">
          {bad.length ? <span className="text-critical">{bad.map((c) => c.name).join(", ")} {bad.length === 1 ? "is" : "are"} failing. Open data sources to see why.</span> : "All sources are reporting normally."}
        </div>
      </div>
      <Link to="/data" className={btnCls}>Manage data sources</Link>
    </Card>
  );
}

function GetStarted({ tenant, isOwner, onLoaded }: { tenant: Tenant; isOwner: boolean; onLoaded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function sample() {
    setBusy(true); setErr(null);
    try { await businessApi.loadDemo(tenant.id); onLoaded(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Could not load sample data."); }
    finally { setBusy(false); }
  }
  return (
    <Card className="!p-6 sm:!p-8">
      <h2 className="display text-2xl">Three ways to get your numbers in</h2>
      <div className="grid md:grid-cols-3 gap-4 mt-5">
        <div className="space-y-2">
          <div className="font-semibold">Connect where your data lives</div>
          <p className="text-sm text-ink-muted">Google Sheets, an Excel or CSV export, a shop system with an API, a database. We detect your columns for you.</p>
          <Link to="/data" className={`${primaryCls} inline-block`}>Connect data</Link>
        </div>
        <div className="space-y-2">
          <div className="font-semibold">Type in a few entries</div>
          <p className="text-sm text-ink-muted">No system yet? Add sales and expenses by hand and the engine starts watching them.</p>
          <Link to="/data#entry" className={`${btnCls} inline-block`}>Add entries</Link>
        </div>
        <div className="space-y-2">
          <div className="font-semibold">Look around with sample data</div>
          <p className="text-sm text-ink-muted">100 days of a small shop's numbers, ending in a sales slump. Remove it any time.</p>
          {isOwner && <button className={btnCls} onClick={sample} disabled={busy}>{busy ? "Loading…" : "Load sample data"}</button>}
        </div>
      </div>
      <div className="mt-3"><Notice error={err} /></div>
    </Card>
  );
}

function OverviewBody({ tenant }: { tenant: Tenant }) {
  const t = useT();
  const isOwner = getClaims().role === "owner";
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [kpis, setKpis] = useState<KpiBreakdown | null>(null);
  const [trend, setTrend] = useState<Trend | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [sources, setSources] = useState<ConnectorRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true); setError(null);
    Promise.all([
      api.listAlerts(tenant.id), api.getKpis(tenant.id, 30), businessApi.trend(tenant.id, 30),
      businessApi.health(tenant.id, 30).catch(() => null), connectorApi.list(tenant.id).catch(() => [] as ConnectorRow[]),
    ])
      .then(([a, k, tr, h, c]) => { setAlerts(a); setKpis(k); setTrend(tr); setHealth(h); setSources(c); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [tenant.id]);
  useEffect(() => { load(); }, [load]);

  const open = alerts.filter((a) => !CLOSED.has(a.state));
  const urgent = open.filter((a) => a.severity === "critical" || a.severity === "high");
  const top = [...open].sort((a, b) => (b.priority_rank ?? 0) - (a.priority_rank ?? 0))[0];
  const noData = !loading && kpis != null && kpis.revenue.current === 0 && kpis.revenue.previous === 0 && kpis.expenses.current === 0 && sources.length === 0;

  let headline = "Nothing needs your attention right now.";
  let sub = health?.headline ?? "Your numbers are being watched. We'll flag anything that moves.";
  if (noData) { headline = "Let's get your numbers in."; sub = "Connect a source or load sample data and this page becomes your daily briefing."; }
  else if (urgent.length && top) {
    headline = top.title;
    sub = urgent.length > 1 ? `${t(urgent.length === 1 ? "dash.high.one" : "dash.high.many", { n: urgent.length })} ${top.what_happened}` : top.what_happened;
  } else if (open.length && top) { headline = "Nothing urgent, but one thing to look at."; sub = top.title; }

  const dot = urgent.some((a) => a.severity === "critical") ? "#d92d20" : urgent.length ? "#e8590c" : "#1f9254";

  return (
    <>
      <section aria-live="polite">
        {loading && !kpis ? <Skeleton className="h-24 w-full max-w-2xl" /> : (
          <>
            <div className="flex items-start gap-3">
              <span className="w-3 h-3 rounded-full shrink-0 mt-3 sm:mt-4" style={{ backgroundColor: noData ? "#8c968f" : dot }} aria-hidden />
              <h1 className="display text-3xl sm:text-5xl max-w-3xl">{headline}</h1>
            </div>
            <p className="text-lg text-ink-muted mt-3 max-w-2xl pl-6">{sub}</p>
            {top && urgent.length > 0 && <div className="pl-6 mt-4"><Link to={`/alert/${top.id}?tenant=${tenant.id}`} className={`${primaryCls} inline-block`}>See why and what to do</Link></div>}
          </>
        )}
      </section>

      {error && <Notice error={`Couldn't load your data: ${error}`} />}

      {noData && <GetStarted tenant={tenant} isOwner={isOwner} onLoaded={load} />}

      {kpis && !noData && (
        <Card className="!p-5 sm:!p-7 space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
            <Kpi label="Sales, last 30 days" value={kpis.revenue.current} delta={change(kpis.revenue.current, kpis.revenue.previous)} goodWhen="up" />
            <Kpi label="Expenses" value={kpis.expenses.current} delta={change(kpis.expenses.current, kpis.expenses.previous)} goodWhen="down" />
            <Kpi label="Profit" value={kpis.profit.current} delta={kpis.profit.change_pct} goodWhen="up" />
          </div>
          {trend && <TrendChart current={chartDays(trend).current} previous={chartDays(trend).previous} />}
          {kpis.largest_contributor && !["Revenue", "Expenses"].includes(kpis.largest_contributor) && <p className="text-sm text-ink-muted border-t border-border pt-4">Biggest driver of the change in profit: <span className="font-semibold text-ink-text">{kpis.largest_contributor}</span>.</p>}
        </Card>
      )}

      {!noData && (
        <section className="space-y-3" aria-label="Needs attention">
          <div className="flex items-end justify-between gap-3">
            <h2 className="text-xl font-semibold tracking-tight">Needs your attention</h2>
            <Link to="/alerts" className="text-sm font-medium text-info hover:underline">All alerts ({open.length} open)</Link>
          </div>
          {loading && !alerts.length && <Skeleton className="h-24 w-full" />}
          {!loading && open.length === 0 && <Empty>No open alerts. When something moves in your numbers, it will show up here with the reason and a suggested step.</Empty>}
          {open.length > 0 && (
            <ul className="bg-panel border border-border rounded-xl divide-y divide-border overflow-hidden">
              {[...open].sort((a, b) => (b.priority_rank ?? 0) - (a.priority_rank ?? 0)).slice(0, 4).map((a) => <li key={a.id}><AlertRow alert={a} tenantId={tenant.id} /></li>)}
            </ul>
          )}
        </section>
      )}

      {!noData && <AskBox key={tenant.id} tenantId={tenant.id} />}
      {sources.length > 0 && <SourcesStrip rows={sources} />}
    </>
  );
}

export function Dashboard() {
  return <Shell wide>{(tenant) => <OverviewBody tenant={tenant} />}</Shell>;
}
