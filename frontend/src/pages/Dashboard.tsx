import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, businessApi, connectorApi, settingsApi, tenantApi, type Alert, type ConnectorRow, type Health, type KpiBreakdown, type Trend } from "../api/client";
import { Shell } from "../components/Shell";
import { AskBox } from "../components/AskBox";
import { DataCheck } from "../components/DataCheck";
import { AlertRow } from "../components/AlertRow";
import { TrendChart } from "../components/TrendChart";
import { Card, Empty, Notice, Skeleton, btnCls, money, primaryCls } from "../components/ui";
import { useT } from "../lib/i18n";
import type { Tenant } from "../api/client";

/** Today usually has no (or partial) data yet; an empty last day would draw a false crash to zero, so leave it out. */
function chartDays(t: Trend): Trend {
  const last = t.current[t.current.length - 1];
  if (last && last.sales === 0 && last.expenses === 0) {
    const n = t.current.length - 1;
    return { ...t, current: t.current.slice(0, n), previous: t.previous.slice(0, n) };
  }
  return t;
}

const PERIODS = [7, 30, 90, 180];

function change(cur: number, prev: number): number | null {
  return prev === 0 ? null : ((cur - prev) / prev) * 100;
}

function Kpi({ label, value, delta, goodWhen, days, cur }: { label: string; value: number; delta: number | null; goodWhen: "up" | "down"; days: number; cur: string }) {
  const good = delta == null || Math.abs(delta) < 0.5 ? null : (delta > 0) === (goodWhen === "up");
  return (
    <div className="min-w-0">
      <div className="text-sm text-ink-muted">{label}</div>
      <div className="figure text-3xl sm:text-4xl mt-0.5 break-words">{money(value, cur)}</div>
      <div className="text-sm mt-1 kpi-figure" style={{ color: good == null ? "var(--neutral)" : good ? "var(--good)" : "var(--bad)" }}>
        {delta == null ? "No earlier records to compare with" : Math.abs(delta) < 0.5 ? `About the same as the ${days} days before` : `${delta > 0 ? "Up" : "Down"} ${Math.abs(delta).toFixed(0)}% compared with the ${days} days before`}
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

function GetStarted() {
  return (
    <Card className="!p-6 sm:!p-8">
      <h2 className="display text-2xl">Two ways to get your numbers in</h2>
      <div className="grid md:grid-cols-2 gap-4 mt-5">
        <div className="space-y-2">
          <div className="font-semibold">Connect where your data lives</div>
          <p className="text-sm text-ink-muted">Google Sheets, an Excel or CSV export, a shop system with an API, a database. We detect your columns for you. Disconnect any time and its data goes with it.</p>
          <Link to="/data" className={`${primaryCls} inline-block`}>Connect data</Link>
        </div>
        <div className="space-y-2">
          <div className="font-semibold">Type in a few entries</div>
          <p className="text-sm text-ink-muted">No system yet? Add sales and expenses by hand and the engine starts watching them.</p>
          <Link to="/data#entry" className={`${btnCls} inline-block`}>Add entries</Link>
        </div>
      </div>
    </Card>
  );
}

function OverviewBody({ tenant }: { tenant: Tenant }) {
  const t = useT();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [kpis, setKpis] = useState<KpiBreakdown | null>(null);
  const [trend, setTrend] = useState<Trend | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [sources, setSources] = useState<ConnectorRow[]>([]);
  const [cur, setCur] = useState("");
  const [channelCount, setChannelCount] = useState<number | null>(null);   // null = unknown (for example a viewer who may not see Settings)
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(7);
  const [showCheck, setShowCheck] = useState(false);

  const latest = useRef(0);       // only the newest request may update the page (a slow answer for 30 days must not replace 90)
  const load = useCallback(() => {
    const mine = ++latest.current;
    setLoading(true); setError(null);
    Promise.all([
      api.listAlerts(tenant.id, { state: "open" }), api.getKpis(tenant.id, days), businessApi.trend(tenant.id, days),
      businessApi.health(tenant.id, days).catch(() => null), connectorApi.list(tenant.id).catch(() => [] as ConnectorRow[]),
      tenantApi.me().then((m) => m.base_currency).catch(() => ""), settingsApi.listChannels(tenant.id).then((l) => l.filter((c) => c.enabled).length).catch(() => null),
    ])
      .then(([a, k, tr, h, c, currency, channels]) => { if (mine === latest.current) { setAlerts(a); setKpis(k); setTrend(tr); setHealth(h); setSources(c); setCur(currency); setChannelCount(channels); } })
      .catch((e) => { if (mine === latest.current) setError(e.message); })
      .finally(() => { if (mine === latest.current) setLoading(false); });
  }, [tenant.id, days]);
  useEffect(() => { load(); }, [load]);

  const open = alerts;
  const urgent = open.filter((a) => a.severity === "critical" || a.severity === "high");
  const top = [...open].sort((a, b) => (b.priority_rank ?? 0) - (a.priority_rank ?? 0))[0];
  const noData = !loading && kpis != null && kpis.revenue.current === 0 && kpis.revenue.previous === 0 && kpis.expenses.current === 0 && sources.length === 0;

  const nothingInView = !loading && kpis != null && sources.length > 0 && kpis.revenue.current === 0 && kpis.revenue.previous === 0 && kpis.expenses.current === 0 && kpis.expenses.previous === 0;

  let headline = "Nothing needs your attention right now.";
  let sub = health?.headline ?? "";
  if (noData) { headline = "Let's get your numbers in."; sub = "Connect a source or add entries by hand and this page becomes your daily briefing."; }
  else if (nothingInView) { headline = "Connected, but there is nothing to show yet."; sub = "Here is exactly what I found, and what to do about it."; }
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
            {sub && <p className="text-lg text-ink-muted mt-3 max-w-2xl pl-6">{sub}</p>}
            {top && urgent.length > 0 && <div className="pl-6 mt-4"><Link to={`/alert/${top.id}?tenant=${tenant.id}`} className={`${primaryCls} inline-block`}>See why and what to do</Link></div>}
          </>
        )}
      </section>

      {error && <Notice error={`Couldn't load your data: ${error}`} />}

      {channelCount === 0 && sources.length > 0 && (
        <Card className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="font-semibold">Nobody is told when something goes wrong</div>
            <div className="text-sm text-ink-muted">Add an email, WhatsApp or SMS number and serious alerts reach you without opening this page.</div>
          </div>
          <Link to="/settings" className={primaryCls}>Set up alerts</Link>
        </Card>
      )}

      {noData && <GetStarted />}

      {(nothingInView || showCheck) && !noData && <DataCheck tenantId={tenant.id} days={days} onShowDays={setDays} />}

      {kpis && !noData && (
        <Card className="!p-5 sm:!p-7 space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div role="group" aria-label="Period shown" className="inline-flex border border-field rounded-sm overflow-hidden">
              {PERIODS.map((d) => (
                <button key={d} aria-pressed={d === days} onClick={() => setDays(d)}
                  className={`px-3 py-1.5 text-sm font-medium ${d === days ? "bg-info text-white" : "bg-panel text-ink-text hover:bg-panel-raised"}`}>
                  {d === 7 ? "Last 7 days" : `${d} days`}
                </button>
              ))}
            </div>
            {!nothingInView && <button className="text-info hover:underline" onClick={() => setShowCheck((v) => !v)}>{showCheck ? "Hide the data check" : "Is something missing? Check my data"}</button>}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
            <Kpi days={days} cur={cur} label={`Sales, last ${days} days`} value={kpis.revenue.current} delta={change(kpis.revenue.current, kpis.revenue.previous)} goodWhen="up" />
            <Kpi days={days} cur={cur} label={`Spending, last ${days} days`} value={kpis.expenses.current} delta={change(kpis.expenses.current, kpis.expenses.previous)} goodWhen="down" />
            <Kpi days={days} cur={cur} label={`Profit, last ${days} days`} value={kpis.profit.current} delta={kpis.profit.change_pct} goodWhen="up" />
          </div>
          {trend && <TrendChart current={chartDays(trend).current} previous={chartDays(trend).previous} />}
          {kpis.largest_contributor && kpis.profit.change_pct != null && <p className="text-sm text-ink-muted border-t border-border pt-4">Biggest effect on your profit compared with the {days} days before: <span className="font-semibold text-ink-text">{kpis.largest_contributor}</span>.</p>}
        </Card>
      )}

      {!noData && (
        <section className="space-y-3" aria-label="Needs attention">
          <div className="flex items-end justify-between gap-3">
            <h2 className="text-xl font-semibold tracking-tight">Needs your attention</h2>
            <Link to="/alerts" className="text-sm font-medium text-info hover:underline">All alerts ({open.length} open)</Link>
          </div>
          {loading && !alerts.length && <Skeleton className="h-24 w-full" />}
          {!loading && open.length === 0 && <Empty>No open alerts.</Empty>}
          {open.length > 0 && (
            <ul className="bg-panel border border-border rounded-xl divide-y divide-border overflow-hidden">
              {[...open].sort((a, b) => (b.priority_rank ?? 0) - (a.priority_rank ?? 0)).slice(0, 4).map((a) => <li key={a.id}><AlertRow alert={a} tenantId={tenant.id} /></li>)}
            </ul>
          )}
        </section>
      )}

      {!noData && <AskBox key={tenant.id} tenantId={tenant.id} days={days} />}
      {sources.length > 0 && <SourcesStrip rows={sources} />}
    </>
  );
}

export function Dashboard() {
  return <Shell wide>{(tenant) => <OverviewBody tenant={tenant} />}</Shell>;
}
