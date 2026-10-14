import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  ApiError, businessApi, settingsApi, tenantApi,
  type ContextItem, type Discovery, type EntityRow, type FxRow, type Health, type Quality, type Relationships, type TenantSettings,
} from "../api/client";
import { getClaims } from "../api/session";
import { Shell } from "../components/Shell";
import { Bar, Card, Empty, Notice, Section, Stat, btnCls, inputCls, money, pct, primaryCls, today } from "../components/ui";
import { SEVERITY_COLOR } from "../lib/severity";
import { useT } from "../lib/i18n";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");
const STATUS_COLOR: Record<string, string> = { healthy: "#4caf7d", watch: "#e8c547", attention: "#f0883e", critical: "#e5484d" };
const PERIODS = [7, 30, 90];

function ScoreRing({ score, color }: { score: number; color: string }) {
  const r = 42, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, score));
  return (
    <svg width="112" height="112" viewBox="0 0 112 112" role="img" aria-label={`Health score ${score} out of 100`}>
      <circle cx="56" cy="56" r={r} fill="none" stroke="#2c3542" strokeWidth="9" />
      <circle cx="56" cy="56" r={r} fill="none" stroke={color} strokeWidth="9" strokeLinecap="round"
        strokeDasharray={`${(v / 100) * c} ${c}`} transform="rotate(-90 56 56)" />
      <text x="56" y="62" textAnchor="middle" fontSize="26" fill="#e8eaed" style={{ fontFamily: "var(--font-mono)" }}>{Math.round(v)}</text>
    </svg>
  );
}

function HealthSection({ tenantId, cur, days, setDays }: { tenantId: string; cur: string; days: number; setDays: (n: number) => void }) {
  const t = useT();
  const [h, setH] = useState<Health | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setErr(null); businessApi.health(tenantId, days).then(setH).catch((e) => setErr(errText(e))); }, [tenantId, days]);
  const color = STATUS_COLOR[h?.status ?? ""] ?? "#5b9bd5";
  return (
    <Section title={t("biz.health")} hint={h?.score_basis}
      action={
        <div role="group" aria-label="Period" className="flex gap-1">
          {PERIODS.map((p) => (
            <button key={p} onClick={() => setDays(p)} aria-pressed={days === p}
              className={`${btnCls} ${days === p ? "!text-ink-text !border-info" : ""}`}>{p}d</button>
          ))}
        </div>
      }>
      <Notice error={err} />
      {h && (
        <>
          <Card accent={color} className="flex flex-wrap items-center gap-6">
            <ScoreRing score={h.score} color={color} />
            <div className="min-w-[200px] flex-1">
              <div className="text-xs uppercase tracking-wide" style={{ color }}>{h.status}</div>
              <p className="text-sm mt-1">{h.headline}</p>
              {h.top_alert && (
                <Link to={`/alert/${h.top_alert.id}`} className="text-xs text-info hover:underline mt-2 inline-block">
                  Most urgent: {h.top_alert.title}
                </Link>
              )}
            </div>
            <div className="text-xs text-ink-muted space-y-1">
              <div>{t("biz.openAlerts")}</div>
              <div className="flex gap-3 kpi-figure">
                {Object.entries(h.open_alerts).length === 0 && <span>none</span>}
                {Object.entries(h.open_alerts).map(([sev, n]) => (
                  <span key={sev} style={{ color: SEVERITY_COLOR[sev] }}>{n} {sev}</span>
                ))}
              </div>
              {h.stale_sources.length > 0 && <div className="text-medium">{t("biz.stale")}: {h.stale_sources.join(", ")}</div>}
            </div>
          </Card>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Stat label={t("biz.sales")} value={money(h.financials.sales, cur)} delta={h.financials.sales_change_pct} />
            <Stat label={t("biz.expenses")} value={money(h.financials.expenses, cur)} delta={h.financials.expenses_change_pct} goodWhen="down" />
            <Stat label={t("biz.profit")} value={money(h.financials.profit, cur)} delta={h.financials.profit_change_pct} />
          </div>
        </>
      )}
    </Section>
  );
}

function QualitySection({ tenantId, days }: { tenantId: string; days: number }) {
  const t = useT();
  const [q, setQ] = useState<Quality | null>(null);
  useEffect(() => { businessApi.quality(tenantId, days).then(setQ).catch(() => setQ(null)); }, [tenantId, days]);
  if (!q) return null;
  return (
    <Section title={t("biz.quality")} hint={`${q.events.toLocaleString()} events checked, ${q.failed} failed validation.`}>
      <div className="grid md:grid-cols-2 gap-3">
        <Card>
          <div className="kpi-figure text-2xl mb-3">{Math.round(q.score)}<span className="text-sm text-ink-muted"> / 100</span></div>
          <div className="space-y-2">
            {Object.entries(q.sales_missing_pct).map(([k, v]) => (
              <Bar key={k} label={`Sales missing ${k}`} value={v} max={100} text={`${v.toFixed(1)}%`} color={v > 20 ? "#e5484d" : v > 5 ? "#e8c547" : "#4caf7d"} />
            ))}
          </div>
        </Card>
        <Card>
          <div className="text-xs text-ink-muted mb-2">By source</div>
          {q.by_connector.length === 0 ? <p className="text-sm text-ink-muted">No data in this period.</p> : (
            <div className="space-y-2">
              {q.by_connector.map((c) => (
                <Bar key={c.connector} label={c.connector} value={c.events - c.failed} max={Math.max(c.events, 1)}
                  text={`${c.events.toLocaleString()} events, ${c.failed_pct.toFixed(1)}% failed`} color={c.failed_pct > 5 ? "#e5484d" : "#4caf7d"} />
              ))}
            </div>
          )}
          {q.top_failure_reasons.length > 0 && (
            <ul className="mt-3 text-xs text-ink-muted list-disc pl-4">{q.top_failure_reasons.map((r) => <li key={r.reason}>{r.reason} ({r.count})</li>)}</ul>
          )}
          {q.notes.map((n) => <p key={n} className="text-xs text-ink-muted mt-2">{n}</p>)}
        </Card>
      </div>
    </Section>
  );
}

function DiscoverySection({ tenantId, isOwner }: { tenantId: string; isOwner: boolean }) {
  const t = useT();
  const [d, setD] = useState<Discovery | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(() => businessApi.discovery(tenantId).then(setD).catch((e) => setErr(errText(e))), [tenantId]);
  useEffect(() => { load(); }, [load]);

  async function monitor(m: Discovery["suggested_metrics"][number]) {
    setErr(null); setNotice(null);
    try {
      await settingsApi.createMetric(tenantId, { entity: m.entity, field: m.field, period_days: m.period_days, threshold_pct: 10, direction: m.direction, absolute_threshold: null });
      setNotice(`Now watching ${m.entity} ${m.field}.`);
      await load();
    } catch (e) { setErr(errText(e)); }
  }
  if (!d) return <Notice error={err} />;
  const cols: [string, keyof Discovery["entities"][number]][] = [["amount", "has_amount_pct"], ["quantity", "has_quantity_pct"], ["customer", "has_customer_pct"], ["product", "has_product_pct"], ["location", "has_location_pct"]];
  return (
    <Section title={t("biz.discovery")} hint="What kinds of records exist, how complete they are, and what could be watched.">
      <Notice error={err} notice={notice} />
      {d.entities.length === 0 ? <Empty>No data yet. Connect a source under Data sources.</Empty> : (
        <div className="overflow-x-auto border border-border rounded-sm">
          <table className="w-full text-xs">
            <thead className="text-ink-muted"><tr className="text-left">
              <th className="p-2">Record</th><th className="p-2">Events</th><th className="p-2">History</th>
              {cols.map(([c]) => <th key={c} className="p-2">Has {c}</th>)}
            </tr></thead>
            <tbody>
              {d.entities.map((e) => (
                <tr key={e.entity} className="border-t border-border">
                  <td className="p-2 font-medium">{e.entity}</td>
                  <td className="p-2 kpi-figure">{e.events.toLocaleString()}</td>
                  <td className="p-2 kpi-figure">{e.span_days} d</td>
                  {cols.map(([c, k]) => <td key={c} className="p-2 kpi-figure" style={{ color: (e[k] as number) < 50 ? "#8b93a1" : "#e8eaed" }}>{(e[k] as number).toFixed(0)}%</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d.suggested_metrics.length > 0 && (
        <Card>
          <div className="text-xs text-ink-muted mb-2">Suggested things to watch</div>
          <ul className="space-y-2">
            {d.suggested_metrics.map((m) => (
              <li key={`${m.entity}.${m.field}`} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span><strong>{m.entity} {m.field}</strong> <span className="text-ink-muted">({m.direction}, {m.period_days}-day) — {m.reason}</span></span>
                {m.already_monitored ? <span className="text-xs text-opportunity">Watching</span>
                  : isOwner && <button className={btnCls} disabled={!m.enough_history} title={m.enough_history ? "" : "Not enough history yet"} onClick={() => monitor(m)}>Watch this</button>}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {d.next_steps.length > 0 && <ul className="text-xs text-ink-muted list-disc pl-4">{d.next_steps.map((n) => <li key={n}>{n}</li>)}</ul>}
    </Section>
  );
}

function RelationshipsSection({ tenantId }: { tenantId: string }) {
  const t = useT();
  const [entities, setEntities] = useState<EntityRow[]>([]);
  const [type, setType] = useState("");
  const [sel, setSel] = useState("");
  const [rel, setRel] = useState<Relationships | null>(null);
  const [days, setDays] = useState(90);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { businessApi.entities(tenantId).then(setEntities).catch(() => setEntities([])); }, [tenantId]);
  const types = useMemo(() => [...new Set(entities.map((e) => e.entity_type))].sort(), [entities]);
  useEffect(() => { if (!type && types[0]) setType(types[0]); }, [types, type]);
  const options = useMemo(() => entities.filter((e) => e.entity_type === type).sort((a, b) => a.canonical_name.localeCompare(b.canonical_name)), [entities, type]);
  useEffect(() => {
    if (!sel) { setRel(null); return; }
    setErr(null);
    businessApi.relationships(tenantId, sel, days).then(setRel).catch((e) => { setRel(null); setErr(errText(e)); });
  }, [tenantId, sel, days]);
  if (entities.length === 0) return null;
  return (
    <Section title={t("biz.relationships")} hint="Pick a customer, product or supplier to see what it is tied to and how much it matters.">
      <div className="flex flex-wrap gap-2">
        <select aria-label="Type" className={`${inputCls} !w-auto`} value={type} onChange={(e) => { setType(e.target.value); setSel(""); }}>
          {types.map((x) => <option key={x}>{x}</option>)}
        </select>
        <select aria-label="Which one" className={`${inputCls} !w-auto max-w-xs`} value={sel} onChange={(e) => setSel(e.target.value)}>
          <option value="">Choose…</option>
          {options.map((o) => <option key={o.id} value={o.id}>{o.canonical_name}</option>)}
        </select>
        <select aria-label="Window" className={`${inputCls} !w-auto`} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[30, 90, 180, 365].map((d) => <option key={d} value={d}>{d} days</option>)}
        </select>
      </div>
      <Notice error={err} />
      {rel && (
        <Card>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mb-3">
            <span className="font-medium">{rel.entity.name}</span>
            <span className="kpi-figure text-sm text-ink-muted">{money(rel.total)} across {rel.events} events</span>
            {rel.share_of_business != null && <span className="text-xs text-info">{pct(rel.share_of_business, 1)} of the business</span>}
          </div>
          <div className="grid sm:grid-cols-2 gap-5">
            {Object.entries(rel.linked).filter(([, rows]) => rows.length > 0).map(([kind, rows]) => (
              <div key={kind}>
                <div className="text-xs text-ink-muted mb-2 capitalize">{kind}</div>
                <div className="space-y-2">
                  {rows.slice(0, 6).map((r) => <Bar key={r.name} label={r.name} value={r.share * 100} max={100} text={`${pct(r.share)} · ${money(r.amount)}`} />)}
                </div>
              </div>
            ))}
          </div>
          {rel.statements.length > 0 && <ul className="mt-3 text-xs text-ink-muted list-disc pl-4">{rel.statements.map((s) => <li key={s}>{s}</li>)}</ul>}
        </Card>
      )}
    </Section>
  );
}

const CONTEXT_KINDS = ["holiday", "promotion", "weather", "event", "other"];

function ContextSection({ tenantId, isOwner }: { tenantId: string; isOwner: boolean }) {
  const t = useT();
  const [items, setItems] = useState<ContextItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [kind, setKind] = useState("promotion");
  const [name, setName] = useState("");
  const [from, setFrom] = useState(today());
  const [to, setTo] = useState("");
  const load = useCallback(() => businessApi.listContext(tenantId).then(setItems).catch((e) => setErr(errText(e))), [tenantId]);
  useEffect(() => { load(); }, [load]);
  async function act(fn: () => Promise<unknown>, done?: string) {
    setErr(null); setNotice(null);
    try { await fn(); if (done) setNotice(done); await load(); } catch (e) { setErr(errText(e)); }
  }
  const add = (e: FormEvent) => { e.preventDefault(); act(async () => { await businessApi.addContext(tenantId, { kind, name: name.trim(), starts_on: from, ends_on: to || null }); setName(""); setTo(""); }, "Added."); };
  return (
    <Section title={t("biz.context")} hint="Holidays and promotions explain swings, so alerts during them are judged fairly."
      action={isOwner ? <button className={btnCls} onClick={() => act(async () => { const r = await businessApi.syncHolidays(tenantId); setNotice(`${r.added} holiday(s) added.`); })}>Load public holidays</button> : undefined}>
      <Notice error={err} notice={notice} />
      {items.length === 0 ? <Empty>{t("common.none")}</Empty> : (
        <ul className="divide-y divide-border border border-border rounded-sm">
          {items.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
              <span><span className="text-xs uppercase text-ink-muted mr-2">{c.kind}</span>{c.name}
                <span className="text-ink-muted kpi-figure"> · {c.starts_on}{c.ends_on && c.ends_on !== c.starts_on ? ` → ${c.ends_on}` : ""}</span></span>
              {isOwner && <button className={btnCls} onClick={() => act(() => businessApi.deleteContext(tenantId, c.id))}>{t("common.remove")}</button>}
            </li>
          ))}
        </ul>
      )}
      {isOwner && (
        <form onSubmit={add} className="flex flex-wrap gap-2 items-end">
          <label className="text-xs text-ink-muted">Kind<select className={`${inputCls} mt-1`} value={kind} onChange={(e) => setKind(e.target.value)}>{CONTEXT_KINDS.map((k) => <option key={k}>{k}</option>)}</select></label>
          <label className="text-xs text-ink-muted flex-1 min-w-[160px]">Name<input required maxLength={200} className={`${inputCls} mt-1`} value={name} onChange={(e) => setName(e.target.value)} placeholder="Easter sale" /></label>
          <label className="text-xs text-ink-muted">From<input required type="date" className={`${inputCls} mt-1`} value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label className="text-xs text-ink-muted">To (optional)<input type="date" min={from} className={`${inputCls} mt-1`} value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <button className={primaryCls}>{t("common.add")}</button>
        </form>
      )}
    </Section>
  );
}

function FxSection({ tenantId, base, isOwner }: { tenantId: string; base: string; isOwner: boolean }) {
  const t = useT();
  const [rows, setRows] = useState<FxRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cur, setCur] = useState("");
  const [rate, setRate] = useState("");
  const [date, setDate] = useState(today());
  const load = useCallback(() => businessApi.listFx(tenantId).then(setRows).catch((e) => setErr(errText(e))), [tenantId]);
  useEffect(() => { load(); }, [load]);
  async function add(e: FormEvent) {
    e.preventDefault(); setErr(null); setNotice(null);
    try {
      await businessApi.putFx(tenantId, { currency: cur.trim().toUpperCase(), rate_to_base: Number(rate), effective_date: date });
      setCur(""); setRate(""); setNotice("Rate saved; waiting events were converted."); await load();
    } catch (x) { setErr(errText(x)); }
  }
  return (
    <Section title={t("biz.fx")} hint={`Rates are units of ${base} per 1 unit of the foreign currency, effective from a date. Events in other currencies convert with the latest rate on or before their date.`}>
      <Notice error={err} notice={notice} />
      {rows.length === 0 ? <Empty>{t("common.none")}</Empty> : (
        <div className="overflow-x-auto border border-border rounded-sm"><table className="w-full text-sm">
          <thead className="text-xs text-ink-muted"><tr className="text-left"><th className="p-2">Currency</th><th className="p-2">1 unit =</th><th className="p-2">From</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.id} className="border-t border-border"><td className="p-2">{r.currency}</td><td className="p-2 kpi-figure">{r.rate_to_base} {base}</td><td className="p-2 kpi-figure">{r.effective_date}</td></tr>)}</tbody>
        </table></div>
      )}
      {isOwner && (
        <form onSubmit={add} className="flex flex-wrap gap-2 items-end">
          <label className="text-xs text-ink-muted">Currency<input required maxLength={3} minLength={3} placeholder="USD" className={`${inputCls} mt-1 !w-24 uppercase`} value={cur} onChange={(e) => setCur(e.target.value)} /></label>
          <label className="text-xs text-ink-muted">Rate to {base}<input required type="number" step="any" min="0" className={`${inputCls} mt-1 !w-32`} value={rate} onChange={(e) => setRate(e.target.value)} /></label>
          <label className="text-xs text-ink-muted">Effective from<input required type="date" className={`${inputCls} mt-1`} value={date} onChange={(e) => setDate(e.target.value)} /></label>
          <button className={primaryCls}>{t("common.save")}</button>
        </form>
      )}
    </Section>
  );
}

export function BusinessPage() {
  const t = useT();
  const isOwner = getClaims().role === "owner";
  const [days, setDays] = useState(30);
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  useEffect(() => { tenantApi.me().then(setSettings).catch(() => setSettings(null)); }, []);
  const cur = settings?.base_currency ?? "";
  return (
    <Shell wide>
      {(tenant) => (
        <>
          <h1 className="text-lg font-semibold tracking-tight">{t("biz.title")}</h1>
          <HealthSection tenantId={tenant.id} cur={cur} days={days} setDays={setDays} />
          <QualitySection tenantId={tenant.id} days={days} />
          <DiscoverySection tenantId={tenant.id} isOwner={isOwner} />
          <RelationshipsSection tenantId={tenant.id} />
          <ContextSection tenantId={tenant.id} isOwner={isOwner} />
          <FxSection tenantId={tenant.id} base={cur || "base currency"} isOwner={isOwner} />
        </>
      )}
    </Shell>
  );
}
