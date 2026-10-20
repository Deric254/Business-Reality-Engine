import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Reconcile } from "../components/Reconcile";
import { RejectedList } from "../components/RejectedList";
import { ApiError, captureApi, connectorApi, importSummary, type CaptureEntry, type ConnectorRow, type Contribution, type ImportResult } from "../api/client";
import { getClaims } from "../api/session";
import { Shell } from "../components/Shell";
import { ConnectWizard, SecretOnce } from "../components/ConnectWizard";
import { Card, Empty, Notice, PageTitle, Section, btnCls, inputCls, primaryCls } from "../components/ui";
import { timeAgo } from "../lib/severity";
import { useT } from "../lib/i18n";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");
const PULL = ["database", "url", "sftp", "email", "shared_folder"];
const TYPE_LABEL: Record<string, string> = { url: "Web link or API", file: "File upload", webhook: "Live feed", database: "Database", sftp: "SFTP", email: "Email", shared_folder: "Shared folder", local_agent: "Local agent", manual: "Typed in by hand" };

function Text({ label, value, onChange, type = "text", required = false }: { label: string; value: string; onChange: (v: string) => void; type?: string; required?: boolean }) {
  return (
    <label className="text-sm text-ink-muted block">{label}{required && " *"}
      <input className={`${inputCls} mt-1`} type={type} value={value} required={required} autoComplete="off" onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function statusOf(c: ConnectorRow): [string, string] {
  if (!c.is_active) return ["Paused", "#8c968f"];
  if (c.last_sync_status === "failed" || c.last_sync_status === "error") return ["Failing", "#d92d20"];
  if (c.last_sync_status === "partial") return ["Working, some rows rejected", "#d4a017"];
  if (c.last_sync_at) return ["Working", "#1f9254"];
  return ["Waiting for data", "#d4a017"];
}

function contributionText(c: Contribution) {
  const parts = [`${c.counted.toLocaleString()} record${c.counted === 1 ? "" : "s"} counted`];
  if (c.matched) parts.push(`${c.matched.toLocaleString()} also in another source (counted once)`);
  if (c.rejected) parts.push(`${c.rejected.toLocaleString()} rejected`);
  return parts.join(", ");
}

function ConnectorList({ tenantId, rows, contributions, isOwner, reload, setNotice, setError }: {
  tenantId: string; rows: ConnectorRow[]; contributions: Record<string, Contribution>; isOwner: boolean; reload: () => void; setNotice: (s: string | null) => void; setError: (s: string | null) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadFor, setUploadFor] = useState<{ id: string; replace: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  async function run(id: string, fn: () => Promise<unknown>, done: (r: unknown) => string) {
    setBusy(id); setError(null); setNotice(null);
    try { const r = await fn(); setNotice(done(r)); reload(); } catch (e) { setError(errText(e)); reload(); } finally { setBusy(null); }
  }
  const summarize = (r: unknown) => importSummary((r ?? {}) as ImportResult);
  /** Sync a source. If a mirrored link now looks completely different, ask the owner before applying it. */
  async function syncSource(c: ConnectorRow) {
    try {
      return await connectorApi.sync(tenantId, c.id, c.connector_type);
    } catch (e) {
      if (isOwner && e instanceof ApiError && e.status === 409 && e.message.startsWith("This source changed a lot") && window.confirm(`${e.message}\n\nReplace the data with what the source holds now?`))
        return connectorApi.sync(tenantId, c.id, c.connector_type, true);
      throw e;
    }
  }
  return (
    <>
      <input ref={fileRef} type="file" hidden accept=".csv,.xlsx,.xls,.json,.xml,.txt"
        onChange={(e) => { const file = e.target.files?.[0]; const target = uploadFor; e.target.value = "";
          if (file && target && (!target.replace || window.confirm(`Replace everything this source holds with ${file.name}? Records that are not in the file are removed.`)))
            run(target.id, () => connectorApi.upload(tenantId, target.id, file, target.replace), summarize); }} />
      <ul className="bg-panel border border-border rounded-xl divide-y divide-border overflow-hidden">
        {rows.map((c) => {
          const [label, color] = statusOf(c);
          return (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 px-4 sm:px-5 py-4">
              <div className="min-w-[200px] flex-1">
                <div className="font-semibold">{c.name}</div>
                <div className="text-sm text-ink-muted">
                  {TYPE_LABEL[c.connector_type] ?? c.connector_type}
                  {c.sync_interval_minutes ? `, checked every ${c.sync_interval_minutes >= 60 ? `${c.sync_interval_minutes / 60} h` : `${c.sync_interval_minutes} min`}` : ""}
                  {c.last_sync_at ? `, last checked ${timeAgo(c.last_sync_at)}` : ""}
                </div>
                {contributions[c.id] && <div className="text-sm text-ink-muted">{contributionText(contributions[c.id])}</div>}
                {contributions[c.id]?.rejected > 0 && <RejectedList key={contributions[c.id].rejected} tenantId={tenantId} connectorId={c.id} count={contributions[c.id].rejected} />}
              </div>
              <span className="text-sm flex items-center gap-2"><span className="w-2 h-2 rounded-full" style={{ background: color }} aria-hidden />{label}</span>
              <div className="flex gap-2">
                {c.connector_type === "file" && <button className={btnCls} disabled={busy === c.id} onClick={() => { setUploadFor({ id: c.id, replace: false }); fileRef.current?.click(); }}>Add a file</button>}
                {c.connector_type === "file" && isOwner && <button className={btnCls} disabled={busy === c.id} title="Makes the file the whole truth: records this source holds that are not in the file are removed" onClick={() => { setUploadFor({ id: c.id, replace: true }); fileRef.current?.click(); }}>Replace with a file</button>}
                {PULL.includes(c.connector_type) && <button className={btnCls} disabled={busy === c.id} title="Re-reads the source. Rows added, edited or deleted there are added, updated or removed here." onClick={() => run(c.id, () => syncSource(c), summarize)}>{busy === c.id ? "Syncing…" : "Sync now"}</button>}
                {isOwner && <button className={btnCls} onClick={() => run(c.id, () => connectorApi.update(tenantId, c.id, { is_active: !c.is_active }), () => (c.is_active ? "Paused." : "Resumed."))}>{c.is_active ? "Pause" : "Resume"}</button>}
                {isOwner && <button className={btnCls} disabled={busy === c.id} onClick={() => {
                  const n = contributions[c.id] ? contributions[c.id].counted + contributions[c.id].matched + contributions[c.id].rejected : 0;
                  if (window.confirm(`Disconnect ${c.name} and delete the ${n} record${n === 1 ? "" : "s"} it brought in? This cannot be undone. To only stop it for now, use Pause.`))
                    run(c.id, () => connectorApi.disconnect(tenantId, c.id), (r) => `Disconnected ${c.name}. ${(r as { removed_records: number }).removed_records} records removed.`);
                }}>Disconnect</button>}
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

export function CaptureForm({ tenantId, onSaved }: { tenantId: string; onSaved?: () => void }) {
  const [entity, setEntity] = useState<"sale" | "expense">("sale");
  const [amount, setAmount] = useState("");
  const [f, setF] = useState({ quantity: "", location: "", customer: "", product: "", supplier: "", note: "" });
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ref, setRef] = useState(() => crypto.randomUUID());
  async function submit(e: FormEvent) {
    e.preventDefault(); setErr(null); setOk(null); setBusy(true);
    const entry: CaptureEntry = { entity, amount: Number(amount), client_ref: ref };      // same ref on a retry: never double-counts
    for (const [k, v] of Object.entries(f)) if (v.trim()) (entry as unknown as Record<string, unknown>)[k] = k === "quantity" ? Number(v) : v.trim();
    try {
      await captureApi.submit(tenantId, [entry]);
      setOk(`Saved ${entity} of ${amount}.`); setAmount(""); setF({ quantity: "", location: "", customer: "", product: "", supplier: "", note: "" }); setRef(crypto.randomUUID()); onSaved?.();
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid sm:grid-cols-3 gap-3">
        <label className="text-sm text-ink-muted">Type
          <select className={`${inputCls} mt-1`} value={entity} onChange={(e) => setEntity(e.target.value as "sale" | "expense")}><option value="sale">Sale</option><option value="expense">Expense</option></select></label>
        <Text label="Amount" type="number" required value={amount} onChange={setAmount} />
        <Text label="Quantity" type="number" value={f.quantity} onChange={set("quantity")} />
        <Text label="Location or branch" value={f.location} onChange={set("location")} />
        <Text label="Product" value={f.product} onChange={set("product")} />
        <Text label={entity === "sale" ? "Customer" : "Supplier"} value={entity === "sale" ? f.customer : f.supplier} onChange={set(entity === "sale" ? "customer" : "supplier")} />
      </div>
      <Text label="Note" value={f.note} onChange={set("note")} />
      <Notice error={err} notice={ok} />
      <button className={primaryCls} disabled={busy || !amount}>{busy ? "Saving…" : "Save entry"}</button>
    </form>
  );
}

function DataBody({ tenantId, isOwner }: { tenantId: string; isOwner: boolean }) {
  const t = useT();
  const [rows, setRows] = useState<ConnectorRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<ConnectorRow | null>(null);
  const [contributions, setContributions] = useState<Record<string, Contribution>>({});
  const load = useCallback(() => Promise.all([connectorApi.list(tenantId), connectorApi.contributions(tenantId)])
    .then(([r, c]) => { setRows(r); setContributions(c); }).catch((e) => setErr(errText(e))), [tenantId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (window.location.hash === "#entry") document.getElementById("entry")?.scrollIntoView(); }, [rows]);
  return (
    <>
      <PageTitle title={t("data.title")} sub="Everything the engine watches comes from here. Connect where your numbers already live, or type them in."
        action={isOwner && !adding ? <button className={primaryCls} onClick={() => { setAdding(true); setNotice(null); }}>{t("data.add")}</button> : undefined} />
      <Notice error={err} notice={notice} />
      {created && (created.webhook_url || created.webhook_token) && <SecretOnce created={created} onClose={() => setCreated(null)} />}
      {adding && isOwner && <ConnectWizard tenantId={tenantId} onClose={() => setAdding(false)} onDone={(c, summary) => { setAdding(false); setCreated(c); setNotice(`Connected ${c.name}.${summary ? " " + summary : ""}`); load(); }} />}
      <Section title="Connected sources">
        {rows.length === 0 && !adding
          ? <Empty action={isOwner ? <button className={primaryCls} onClick={() => setAdding(true)}>{t("data.add")}</button> : undefined}>No data source connected yet. Connect one, or add entries by hand below.</Empty>
          : <ConnectorList tenantId={tenantId} rows={rows} contributions={contributions} isOwner={isOwner} reload={load} setNotice={setNotice} setError={setErr} />}
      </Section>
      <Reconcile tenantId={tenantId} isOwner={isOwner} rows={rows} onChanged={load} setNotice={setNotice} setError={setErr} />
      <div id="entry" />
      <Section title={t("data.capture")} hint="For a business with no system yet: one-off sales and expenses. Retrying a save never double-counts.">
        <Card><CaptureForm tenantId={tenantId} /></Card>
      </Section>
    </>
  );
}

export function DataPage() {
  const isOwner = getClaims().role === "owner";
  return <Shell>{(tenant) => <DataBody tenantId={tenant.id} isOwner={isOwner} />}</Shell>;
}
