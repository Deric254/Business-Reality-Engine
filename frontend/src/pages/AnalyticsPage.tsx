import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, businessApi, tenantApi, type AInsight, type Alert, type Analytics, type Tenant } from "../api/client";
import { Shell } from "../components/Shell";
import { AlertRow } from "../components/AlertRow";
import { Empty, Notice, Skeleton, btnCls, money, primaryCls } from "../components/ui";
import { CalendarHeatmap, ChartCard, Change, ForecastChart, HRow, Legend, MarginChart, MonthlyChart, Spark, WeekdayChart } from "../components/charts";
import { C, dayLong, dayShort, fmtFull } from "../lib/chartKit";
import { useOnLiveChange } from "../lib/liveContext";

const PERIODS = [1, 7, 30, 90, 180, 365, 0];
const periodLabel = (d: number) => (d === 0 ? "All time" : d === 1 ? "Today" : d === 365 ? "12 months" : `${d} days`);
const before = (d: number) => (d === 0 ? "" : d === 1 ? " on yesterday" : ` on the ${d} days before`);

/* ---------------------------------------------------------------- headline tiles */

function Tile({ label, value, sub, spark, sparkColor, children }: { label: string; value: string; sub?: React.ReactNode; spark?: number[]; sparkColor?: string; children?: React.ReactNode }) {
  return (
    <div className="bg-panel border border-border rounded-xl p-4 sm:p-5 min-w-0 break-inside-avoid">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm text-ink-muted">{label}</div>
        {spark && <Spark values={spark} color={sparkColor} />}
      </div>
      <div className="figure text-2xl sm:text-3xl mt-1 whitespace-nowrap" style={{ fontVariantNumeric: "lining-nums proportional-nums" }}>{value}</div>
      <div className="text-sm mt-1.5">{sub}</div>
      {children}
    </div>
  );
}

/** Daily records of the period, bucketed so a long period still draws a readable line. */
function series(a: Analytics, pick: (d: { sales: number; spending: number }) => number): number[] {
  const first = a.period.first_day;
  const days = a.daily.filter((d) => d.date >= first && d.date <= a.period.last_day);
  const size = days.length <= 45 ? 1 : days.length <= 120 ? 3 : 7;
  const out: number[] = [];
  for (let i = 0; i < days.length; i += size) out.push(days.slice(i, i + size).reduce((s, d) => s + pick(d), 0));
  return out;
}

function Insight({ i }: { i: AInsight }) {
  const tone = {
    good: { c: C.good, icon: <path d="m5 12.5 4.2 4.2L19 7" /> },
    warn: { c: "#8a5a00", icon: <><path d="M12 4 3 20h18z" /><path d="M12 10v4.5M12 17.5v.01" /></> },
    bad: { c: C.bad, icon: <><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></> },
    info: { c: "#46524d", icon: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8v.01" /></> },
  }[i.tone];
  return (
    <li className="flex gap-3 py-3">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={tone.c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5" aria-hidden>{tone.icon}</svg>
      <div className="min-w-0">
        <div className="font-semibold text-ink-text leading-snug">{i.title}</div>
        <p className="text-sm text-ink-muted mt-0.5">{i.text}</p>
      </div>
    </li>
  );
}

/* ---------------------------------------------------------------- download */

function csvOf(a: Analytics, cur: string): string {
  const q = (v: string | number | null) => (v == null ? "" : `"${String(v).replace(/"/g, '""')}"`);
  const lines: string[] = [];
  const sect = (title: string, head: string[], rows: (string | number | null)[][]) => { lines.push(q(title), head.map(q).join(","), ...rows.map((r) => r.map(q).join(",")), ""); };
  sect(`Summary (${a.period.first_day} to ${a.period.last_day}, ${cur})`, ["Measure", "This period", "Period before", "Change %"], [
    ["Sales", a.headline.sales.current, a.headline.sales.previous, a.headline.sales.change_pct], ["Spending", a.headline.spending.current, a.headline.spending.previous, a.headline.spending.change_pct],
    ["Profit", a.headline.profit.current, a.headline.profit.previous, a.headline.profit.change_pct], ["Margin %", a.headline.margin_pct.current, a.headline.margin_pct.previous, null],
    ["Average sale", a.headline.average_sale.current, a.headline.average_sale.previous, a.headline.average_sale.change_pct]]);
  sect("By month", ["Month", "Sales", "Spending", "Profit", "Margin %"], a.monthly.map((m) => [m.month.slice(0, 7), m.sales, m.spending, m.profit, m.margin_pct]));
  sect("By branch", ["Branch", "Sales", "Spending", "Profit", "Margin %", "Share of sales %", "Sales change %"], a.branches.map((b) => [b.name, b.sales, b.spending, b.profit, b.margin_pct, b.share_pct, b.sales_change_pct]));
  sect("Products", ["Product", "Sales", "Share %", "Cumulative %", "Change %"], a.products.top.map((p) => [p.name, p.amount, p.share_pct ?? null, p.cumulative_pct ?? null, p.change_pct]));
  sect("Spending by category", ["Category", "Amount", "Share %", "Period before", "Change %"], a.spending.categories.map((c) => [c.name, c.amount, c.share_pct ?? null, c.previous, c.change_pct]));
  sect("Customers", ["Customer", "Sales", "Orders", "Share %"], a.customers.top.map((c) => [c.name, c.amount, c.orders, c.share_pct]));
  return lines.join("\n");
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---------------------------------------------------------------- the page */

function Body({ tenant }: { tenant: Tenant }) {
  const [days, setDays] = useState(90);
  const [a, setA] = useState<Analytics | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [cur, setCur] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pdf, setPdf] = useState<"idle" | "busy" | "failed">("idle");
  const latest = useRef(0);                                   // only the newest request may update the page

  const load = useCallback((quiet = false) => {          // quiet: refreshed because the data changed, so nothing spins or dims
    const mine = ++latest.current;
    if (!quiet) setLoading(true);
    setError(null);
    Promise.all([businessApi.analytics(tenant.id, days), api.listAlerts(tenant.id, { state: "open" }).catch(() => [] as Alert[]), tenantApi.me().then((m) => m.base_currency).catch(() => "")])
      .then(([an, al, c]) => { if (mine === latest.current) { setA(an); setAlerts(al); setCur(c); } })
      .catch((e) => { if (mine === latest.current) setError(e.message); })
      .finally(() => { if (mine === latest.current) setLoading(false); });
  }, [tenant.id, days]);
  useOnLiveChange(() => load(true));
  useEffect(() => { load(); }, [load]);
  const periodBar = useRef<HTMLDivElement>(null);
  useEffect(() => {                                           // where the period row scrolls (phones), keep the chosen period in view
    const bar = periodBar.current, on = bar?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!bar || !on) return;
    const barBox = bar.getBoundingClientRect(), onBox = on.getBoundingClientRect();
    bar.scrollLeft += onBox.left - barBox.left - (barBox.width - onBox.width) / 2;
  }, [days]);

  const stale = loading && a != null;                         // keep the last picture on screen, dimmed, while a new period loads
  const top = useMemo(() => [...alerts].sort((x, y) => (y.priority_rank ?? 0) - (x.priority_rank ?? 0)), [alerts]);
  const empty = a != null && a.headline.sales.current === 0 && a.headline.spending.current === 0 && a.monthly.every((m) => m.sales === 0 && m.spending === 0);
  const m = (n: number) => money(n, cur);
  const sofar = <span className="text-ink-muted">so far today; the day is not over</span>;

  async function downloadPdf() {
    if (!a) return;
    setPdf("busy");
    try {
      const url = URL.createObjectURL(await businessApi.analyticsPdf(tenant.id, a.days));
      const link = document.createElement("a");
      link.href = url; link.download = `analytics-${a.period.first_day}-to-${a.period.last_day}.pdf`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setPdf("idle");
    } catch { setPdf("failed"); }
  }

  return (
    <>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="display text-3xl sm:text-4xl">Analytics</h1>
          <p className="text-ink-muted mt-1.5 max-w-prose">
            {a ? <>{dayShort(a.period.first_day)} to {dayLong(a.period.last_day)}. {a.period.days === 1 ? "Today counts up to the latest records that passed validation." : "Complete days only, ending yesterday on your business's clock, so today's records count from tomorrow. Records that passed validation."}</> : "Loading your figures."}
          </p>
          <p className="hidden print:block text-sm mt-1">{tenant.name}. Prepared {new Date().toLocaleDateString("en-KE", { day: "numeric", month: "long", year: "numeric" })}.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 min-w-0 max-w-full print:hidden">
          <div ref={periodBar} role="group" aria-label="Period shown" className="inline-flex max-w-full min-w-0 border border-field rounded-sm overflow-x-auto">
            {PERIODS.map((d) => <button key={d} aria-pressed={d === days} onClick={() => setDays(d)} className={`shrink-0 whitespace-nowrap px-3 py-1.5 text-sm font-medium ${d === days ? "bg-info text-white" : "bg-panel text-ink-text hover:bg-panel-raised"}`}>{periodLabel(d)}</button>)}
          </div>
          <button className={btnCls} disabled={!a} onClick={() => a && download(`analytics-${a.period.first_day}-to-${a.period.last_day}.csv`, csvOf(a, cur))}>Download figures</button>
          <button className={btnCls} disabled={!a || pdf === "busy"} onClick={downloadPdf}>{pdf === "busy" ? "Preparing PDF…" : "Download PDF"}</button>
        </div>
      </header>

      {error && <Notice error={`Couldn't load your analytics: ${error}`} />}
      {pdf === "failed" && <Notice error="Couldn't prepare the PDF. Please try again." />}
      {loading && !a && <div className="space-y-6" aria-busy="true"><Skeleton className="h-32 w-full" /><Skeleton className="h-72 w-full" /><Skeleton className="h-72 w-full" /></div>}

      {a && empty && (
        <Empty action={<Link to="/data" className={`${primaryCls} inline-block`}>Connect data</Link>}>
          There are no sales or spending records yet, so there is nothing to analyse. Connect a sheet or file and this page fills in.
        </Empty>
      )}

      {a && !empty && (
        <div className={`space-y-9 transition-opacity ${stale ? "opacity-60" : ""}`} aria-busy={stale}>
          {days > 1 && a.headline.sales.current === 0 && a.headline.spending.current === 0 && (
            <p role="status" className="text-sm text-ink-text bg-panel border border-border rounded-sm px-3 py-2">No complete days are recorded in this period yet. Today's records count from tomorrow; choose Today to see them now.</p>
          )}
          {days === 1 && a.headline.sales.current === 0 && a.headline.spending.current === 0 && (
            <p role="status" className="text-sm text-ink-text bg-panel border border-border rounded-sm px-3 py-2">Nothing has been recorded today yet. New records show here after your next sync or upload. The charts below still show your recent history.</p>
          )}
          {/* Headline figures */}
          <section aria-label="Headline figures" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <Tile label={`Sales, ${periodLabel(days).toLowerCase()}`} value={m(a.headline.sales.current)} spark={series(a, (d) => d.sales)} sparkColor={C.sales}
              sub={days === 1 ? sofar : <><Change pct={a.headline.sales.change_pct} goodWhen="up" /> <span className="text-ink-muted">{before(days) || "all your records"}</span></>} />
            <Tile label={`Spending, ${periodLabel(days).toLowerCase()}`} value={m(a.headline.spending.current)} spark={series(a, (d) => d.spending)} sparkColor={C.spending}
              sub={days === 1 ? sofar : <><Change pct={a.headline.spending.change_pct} goodWhen="down" /> <span className="text-ink-muted">{before(days) || "all your records"}</span></>} />
            <Tile label={`Profit, ${periodLabel(days).toLowerCase()}`} value={m(a.headline.profit.current)} spark={series(a, (d) => d.sales - d.spending)} sparkColor={C.profit}
              sub={days === 1 ? sofar : <><Change pct={a.headline.profit.change_pct} goodWhen="up" /> <span className="text-ink-muted">{before(days) || "after recorded spending"}</span></>} />
            <Tile label="Profit margin" value={a.headline.margin_pct.current == null ? "No sales" : `${a.headline.margin_pct.current.toFixed(1)}%`}
              spark={a.monthly.map((x) => x.margin_pct ?? 0)} sparkColor={C.profit}
              sub={days === 1 ? sofar : a.headline.margin_pct.change_points == null ? <span className="text-ink-muted">Kept from each sale after spending</span>
                : <span className="kpi-figure" style={{ color: a.headline.margin_pct.change_points >= 0 ? C.good : C.bad }}>{a.headline.margin_pct.change_points >= 0 ? "▲" : "▼"} {Math.abs(a.headline.margin_pct.change_points).toFixed(1)} points <span className="text-ink-muted font-normal">{before(days)}</span></span>} />
            <Tile label="Average sale" value={m(a.headline.average_sale.current)}
              sub={days === 1 ? sofar : <><Change pct={a.headline.average_sale.change_pct} goodWhen="up" /> <span className="text-ink-muted">{a.headline.transactions.current.toLocaleString("en-US")} sales{days ? before(days) : ""}</span></>} />
            <Tile label="Customers who bought" value={a.headline.customers.current.toLocaleString("en-US")}
              sub={days === 1 ? sofar : <><Change pct={a.headline.customers.change_pct} goodWhen="up" /> <span className="text-ink-muted">{a.customers.repeat_rate_pct != null ? `${a.customers.repeat_rate_pct.toFixed(0)}% of named customers came back` : before(days)}</span></>} />
          </section>

          {/* What needs attention: the same alerts the engine raised from these records */}
          <section className="space-y-3" aria-label="Needs attention">
            <div className="flex items-end justify-between gap-3">
              <h2 className="text-xl font-semibold tracking-tight">Needs your attention</h2>
              <Link to="/alerts" className="text-sm font-medium text-info hover:underline print:hidden py-2 lg:py-0">All alerts ({alerts.length} open)</Link>
            </div>
            {top.length === 0
              ? <Empty>No open alerts. Nothing in your records is outside its usual range.</Empty>
              : <ul className="bg-panel border border-border rounded-xl divide-y divide-border overflow-hidden">{top.slice(0, 3).map((al) => <li key={al.id}><AlertRow alert={al} tenantId={tenant.id} /></li>)}</ul>}
          </section>

          {/* Insights: sentences built from the figures below */}
          {a.insights.length > 0 && (
            <section className="bg-panel border border-border rounded-xl p-5 break-inside-avoid" aria-label="What your records say">
              <h2 className="text-xl font-semibold tracking-tight">What your records say</h2>
              <p className="text-sm text-ink-muted mt-0.5">Each line is worked out from the figures on this page, not guessed.</p>
              <ul className="divide-y divide-border mt-2 md:columns-2 md:gap-x-10 [&>li]:break-inside-avoid">{a.insights.map((i) => <Insight key={i.title} i={i} />)}</ul>
            </section>
          )}

          <ChartCard title="Sales, spending and profit by month" sub="The shape of the year. Profit is what is left after the spending you recorded. Not affected by the period buttons."
            table={{ head: ["Month", "Sales", "Spending", "Profit", "Margin"], numeric: [1, 2, 3, 4], rows: a.monthly.map((x) => [x.month.slice(0, 7) + (x.partial ? " (so far)" : ""), x.sales, x.spending, x.profit, x.margin_pct == null ? "—" : `${x.margin_pct.toFixed(0)}%`]) }}>
            <MonthlyChart rows={a.monthly} cur={cur} />
          </ChartCard>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <ChartCard title="How much of each sale you keep" sub="Profit margin by month. A falling line means costs are growing faster than sales. Not affected by the period buttons."
              table={{ head: ["Month", "Margin"], numeric: [1], rows: a.monthly.map((x) => [x.month.slice(0, 7), x.margin_pct == null ? "—" : `${x.margin_pct.toFixed(1)}%`]) }}>
              <MarginChart rows={a.monthly} />
            </ChartCard>
            <ChartCard title="Where weekly sales are heading"
              sub={a.forecast.projection.length ? `A straight-line view of your last ${a.forecast.weeks.length} complete weeks.${a.forecast.reliable ? "" : " Weekly sales swing a lot, so read the range, not the middle line."} Not affected by the period buttons.` : a.forecast.note}
              table={a.forecast.weeks.length ? { head: ["Week from", "Sales", "Spending", "Outlook"], numeric: [1, 2, 3], rows: [...a.forecast.weeks.map((x) => [x.week_start, x.sales, x.spending, "—"]), ...a.forecast.projection.map((p) => [p.week_start, "—", "—", `${fmtFull(p.low)} to ${fmtFull(p.high)}`])] } : undefined}>
              {a.forecast.weeks.length >= 2 ? <ForecastChart weeks={a.forecast.weeks} projection={a.forecast.projection} cur={cur} /> : <p className="text-sm text-ink-muted py-10 text-center">Needs a few complete weeks of sales before it can draw anything.</p>}
              {a.forecast.r_squared != null && <p className="text-xs text-ink-faint mt-2">How well a straight line fits: {(a.forecast.r_squared * 100).toFixed(0)}%. Weekly sales vary about {a.forecast.weekly_noise_pct?.toFixed(0)}% around their average.</p>}
            </ChartCard>
          </div>

          {/* Branches */}
          {a.branches.length > 0 && (
            <ChartCard title="Branch by branch" sub="Sales and recorded spending side by side, with what each branch leaves you. Spending with no branch recorded is not counted here."
              table={{ head: ["Branch", "Sales", "Spending", "Profit", "Margin", "Share of sales", "Sales vs before"], numeric: [1, 2, 3, 4, 5, 6], rows: a.branches.map((b) => [b.name, b.sales, b.spending, b.profit, b.margin_pct == null ? "—" : `${b.margin_pct.toFixed(0)}%`, b.share_pct == null ? "—" : `${b.share_pct.toFixed(0)}%`, b.sales_change_pct == null ? "—" : `${b.sales_change_pct.toFixed(0)}%`]) }}>
              <div className="divide-y divide-border">
                {(() => {
                  const max = Math.max(1, ...a.branches.flatMap((b) => [b.sales, b.spending]));
                  return a.branches.map((b) => (
                    <div key={b.name} className="grid md:grid-cols-[minmax(0,11rem)_1fr_minmax(0,11rem)] gap-x-6 gap-y-2 py-3.5 items-center">
                      <div><div className="font-semibold text-ink-text">{b.name}</div><div className="text-xs text-ink-muted">{b.share_pct != null ? `${b.share_pct.toFixed(0)}% of sales` : ""}{b.sales_change_pct != null && <> · <Change pct={b.sales_change_pct} goodWhen="up" /></>}</div></div>
                      <div>
                        <HRow label="Sales" value={b.sales} max={max} text={m(b.sales)} color={C.sales} />
                        <HRow label="Spending" value={b.spending} max={max} text={m(b.spending)} color={C.spending} />
                      </div>
                      <div className="md:text-right">
                        <div className="kpi-figure text-lg" style={{ color: b.profit < 0 ? C.bad : C.text }}>{b.profit < 0 ? "▼ " : ""}{m(b.profit)}</div>
                        <div className="text-xs text-ink-muted">{b.profit < 0 ? "Loss" : "Profit"}{b.margin_pct != null && ` · keeps ${b.margin_pct.toFixed(0)}% of sales`}</div>
                      </div>
                    </div>
                  ));
                })()}
              </div>
              <Legend items={[{ color: C.sales, label: "Sales" }, { color: C.spending, label: "Spending" }]} />
            </ChartCard>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Products */}
            <ChartCard title="What sells" sub={a.products.products_for_80pct ? `${a.products.products_for_80pct} of ${a.products.count} products make 80% of sales. Those are shaded teal.` : "Products by sales."}
              table={{ head: ["Product", "Sales", "Share", "Running total", "vs before"], numeric: [1, 2, 3, 4], rows: a.products.top.map((p) => [p.name, p.amount, `${p.share_pct?.toFixed(1)}%`, `${p.cumulative_pct?.toFixed(0)}%`, p.change_pct == null ? "—" : `${p.change_pct.toFixed(0)}%`]) }}>
              {a.products.top.length === 0 ? <p className="text-sm text-ink-muted py-8 text-center">No product is recorded on your sales.</p> : (
                <div>
                  {a.products.top.map((p, i) => (
                    <HRow key={p.name} label={p.name} value={p.amount} max={a.products.top[0].amount} text={m(p.amount)} color={a.products.products_for_80pct != null && i < a.products.products_for_80pct ? C.sales : C.muted}
                      trailing={<><span className="text-ink-text kpi-figure">{p.share_pct?.toFixed(0)}%</span>{p.change_pct != null && <div><Change pct={p.change_pct} goodWhen="up" /></div>}</>} />
                  ))}
                  {a.products.others.count > 0 && <p className="text-xs text-ink-faint mt-2">And {a.products.others.count} more products, {a.products.others.share_pct?.toFixed(0)}% of sales together.</p>}
                </div>
              )}
            </ChartCard>

            {/* Spending */}
            <ChartCard title="Where the money goes" sub={`${m(a.spending.total)} spent. The arrow compares each category${before(days) || " with before"}.`}
              table={{ head: ["Category", "Spent", "Share", "Period before", "Change"], numeric: [1, 2, 3, 4], rows: a.spending.categories.map((c) => [c.name, c.amount, `${c.share_pct?.toFixed(1)}%`, c.previous == null ? "—" : c.previous, c.change_pct == null ? "—" : `${c.change_pct.toFixed(0)}%`]) }}>
              {a.spending.categories.length === 0 ? <p className="text-sm text-ink-muted py-8 text-center">No spending recorded in this period.</p> : (
                <div>
                  {a.spending.categories.map((c) => (
                    <HRow key={c.name} label={c.name} value={c.amount} max={a.spending.categories[0].amount} text={m(c.amount)} color={C.spending}
                      trailing={<><span className="text-ink-text kpi-figure">{c.share_pct?.toFixed(0)}%</span>{c.previous != null && <div><Change pct={c.change_pct} goodWhen="down" /></div>}</>} />
                  ))}
                </div>
              )}
            </ChartCard>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <ChartCard title="Which days carry the business" sub="Average sales on each weekday of the period, counting closed days as zero."
              table={{ head: ["Day", "Average sales", "Days counted"], numeric: [1, 2], rows: a.weekdays.map((d) => [d.day, d.average_sales, d.days]) }}>
              {a.weekdays.length ? <WeekdayChart rows={a.weekdays} cur={cur} /> : <p className="text-sm text-ink-muted py-8 text-center">Needs at least two weeks in the period to compare weekdays. Choose a longer period.</p>}
            </ChartCard>

            <ChartCard title="Who buys" sub={a.customers.top_customer_share_pct != null ? `Your biggest named customer is ${a.customers.top_customer_share_pct.toFixed(0)}% of sales; the top five are ${a.customers.top5_share_pct?.toFixed(0)}%.` : "Customers by sales."}
              table={{ head: ["Customer", "Sales", "Orders", "Share"], numeric: [1, 2, 3], rows: a.customers.top.map((c) => [c.name + (c.generic ? " (walk-in)" : ""), c.amount, c.orders, c.share_pct == null ? "—" : `${c.share_pct.toFixed(1)}%`]) }}>
              {a.customers.top.length === 0 ? <p className="text-sm text-ink-muted py-8 text-center">No customer is recorded on your sales.</p> : (
                <div>
                  {a.customers.top.map((c) => <HRow key={c.name} label={c.name} sub={c.generic ? "Walk-in buyers, not one customer" : `${c.orders} order${c.orders === 1 ? "" : "s"}`} value={c.amount} max={a.customers.top[0].amount} text={m(c.amount)} color={c.generic ? C.muted : C.sales}
                    trailing={<span className="text-ink-text kpi-figure">{c.share_pct?.toFixed(0)}%</span>} />)}
                  {a.customers.new_customers != null && a.customers.named_count > 0 && <p className="text-xs text-ink-faint mt-2">{a.customers.new_customers} new and {a.customers.returning_customers} returning named customers in this period.</p>}
                </div>
              )}
            </ChartCard>
          </div>

          <ChartCard title="Every day, the last 26 weeks" sub="Darker days sold more. Quiet stretches and gaps in your records show up at once. Not affected by the period buttons."
            table={{ head: ["Day", "Sales", "Spending"], numeric: [1, 2], rows: [...a.daily].sort((x, y) => y.sales - x.sales).slice(0, 12).map((d) => [dayLong(d.date), d.sales, d.spending]) }}>
            <CalendarHeatmap daily={a.daily} lastDay={a.period.today} cur={cur} />
          </ChartCard>

          <p className="text-xs text-ink-faint">
            Profit here is sales minus the spending you recorded, so stock purchases count as spending when they are paid. Records that failed validation, and duplicates of a record already counted, are left out of every figure.
          </p>
        </div>
      )}
    </>
  );
}

export function AnalyticsPage() {
  return <Shell wide>{(tenant) => <Body tenant={tenant} />}</Shell>;
}
