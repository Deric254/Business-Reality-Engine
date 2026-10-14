import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, captureApi, connectorApi, type CaptureEntry, type ConnectorRow, type DbType } from "../api/client";
import { getClaims } from "../api/session";
import { Shell } from "../components/Shell";
import { Card, Empty, Notice, Section, btnCls, inputCls, primaryCls } from "../components/ui";
import { useT } from "../lib/i18n";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");

export const SOURCE_TYPES: { id: string; label: string; help: string }[] = [
  { id: "file", label: "Spreadsheet upload (CSV / Excel)", help: "Upload exports whenever you like. You map your columns once." },
  { id: "webhook", label: "Live feed (webhook)", help: "Your system posts each sale or expense to a private link as it happens." },
  { id: "database", label: "Database (read-only query)", help: "We run one fixed SELECT on a schedule. Give us a read-only login." },
  { id: "sftp", label: "SFTP folder", help: "Pick up exported files from an SFTP server." },
  { id: "email", label: "Email attachments", help: "Read reports your system emails to a mailbox." },
  { id: "shared_folder", label: "Shared folder on the server", help: "Files dropped into your business folder by a sync tool or the local agent." },
];

type Fields = Record<string, string>;

function Text({ label, value, onChange, type = "text", required = false, placeholder, autoComplete }: {
  label: string; value: string; onChange: (v: string) => void; type?: string; required?: boolean; placeholder?: string; autoComplete?: string;
}) {
  return (
    <label className="text-xs text-ink-muted block">{label}{required && " *"}
      <input className={`${inputCls} mt-1`} type={type} value={value} required={required} placeholder={placeholder} autoComplete={autoComplete ?? "off"}
        onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** Column mapping shared by file / database / sftp / email / folder sources; webhooks use paths instead of columns. */
function MappingFields({ f, set, webhook }: { f: Fields; set: (k: string, v: string) => void; webhook: boolean }) {
  const suffix = webhook ? "_path" : "_col";
  const noun = webhook ? "path in the JSON, e.g. data.amount" : "column name";
  const rows: [string, string, boolean][] = [
    [webhook ? "timestamp_path" : "timestamp_col", "When it happened", !webhook],
    [`amount${suffix}`, "Amount", false], [`quantity${suffix}`, "Quantity", false], [`currency${suffix}`, "Currency", false],
    [`location${suffix}`, "Location / branch", false], [`customer_id${suffix}`, "Customer", false], [`product_id${suffix}`, "Product", false],
    [`entity_id${suffix}`, "Unique record id (prevents duplicates)", false],
  ];
  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium text-ink-text mb-1">What do your {webhook ? "fields" : "columns"} mean?</legend>
      <div className="grid sm:grid-cols-2 gap-2">
        <label className="text-xs text-ink-muted">Record type *
          <select className={`${inputCls} mt-1`} value={f.entity} onChange={(e) => { set("entity", e.target.value); set("event_type", `${e.target.value}.created`); }}>
            <option value="sale">Sale</option><option value="expense">Expense</option>
          </select></label>
        <Text label="Currency if the data has none" value={f.default_currency} onChange={(v) => set("default_currency", v.toUpperCase())} />
        {rows.map(([k, label, req]) => <Text key={k} label={`${label} — ${noun}`} value={f[k] ?? ""} onChange={(v) => set(k, v)} required={req} />)}
      </div>
    </fieldset>
  );
}

function AddSource({ tenantId, onDone }: { tenantId: string; onDone: (created: ConnectorRow) => void }) {
  const [type, setType] = useState("file");
  const [name, setName] = useState("");
  const [f, setF] = useState<Fields>({ entity: "sale", event_type: "sale.created", default_currency: "KES", port: "", db_type: "postgres", mailbox: "INBOX", file_pattern: "*", use_ssl: "true" });
  const [freq, setFreq] = useState("");
  const [interval, setIntervalMin] = useState("");
  const [dbTypes, setDbTypes] = useState<DbType[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));
  useEffect(() => { if (type === "database") connectorApi.dbTypes(tenantId).then(setDbTypes).catch(() => setDbTypes([])); }, [type, tenantId]);

  async function submit(e: FormEvent) {
    e.preventDefault(); setErr(null); setBusy(true);
    const config: Record<string, unknown> = {};
    const mappingKeys = Object.keys(f).filter((k) => /(_col|_path)$/.test(k) || k === "entity" || k === "event_type" || k === "default_currency");
    for (const k of mappingKeys) if (f[k]) config[k] = f[k];
    const num = (k: string) => (f[k] ? Number(f[k]) : undefined);
    if (type === "database") Object.assign(config, { db_type: f.db_type, host: f.host ?? "", port: num("port") ?? null, database: f.database ?? "", username: f.username ?? "", password: f.password ?? "", query: f.query ?? "" });
    if (type === "sftp") Object.assign(config, { host: f.host, port: num("port") ?? 22, username: f.username, password: f.password, remote_directory: f.remote_directory, file_pattern: f.file_pattern || "*", ...(f.host_key_sha256 ? { host_key_sha256: f.host_key_sha256 } : {}) });
    if (type === "email") Object.assign(config, { imap_host: f.imap_host, imap_port: num("port") ?? 993, use_ssl: f.use_ssl !== "false", username: f.username, password: f.password, mailbox: f.mailbox || "INBOX", ...(f.from_filter ? { from_filter: f.from_filter } : {}), ...(f.subject_filter ? { subject_filter: f.subject_filter } : {}) });
    if (type === "shared_folder") Object.assign(config, { path: f.path ?? "", file_pattern: f.file_pattern || "*" });
    try {
      const created = await connectorApi.create({
        tenant_id: tenantId, name, connector_type: type, config, expected_frequency: freq || null,
        sync_interval_minutes: ["database", "sftp", "email", "shared_folder"].includes(type) && interval ? Number(interval) : null,
      });
      onDone(created);
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }

  const help = SOURCE_TYPES.find((s) => s.id === type)?.help;
  const pull = ["database", "sftp", "email", "shared_folder"].includes(type);
  const selected = dbTypes.find((d) => d.db_type === f.db_type);
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid sm:grid-cols-2 gap-2">
        <label className="text-xs text-ink-muted">Source type
          <select className={`${inputCls} mt-1`} value={type} onChange={(e) => setType(e.target.value)}>
            {SOURCE_TYPES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select></label>
        <Text label="Name" value={name} onChange={setName} required placeholder="Main till" />
      </div>
      {help && <p className="text-xs text-ink-muted">{help}</p>}

      {type === "database" && (
        <div className="grid sm:grid-cols-2 gap-2">
          <label className="text-xs text-ink-muted">Database engine
            <select className={`${inputCls} mt-1`} value={f.db_type} onChange={(e) => set("db_type", e.target.value)}>
              {(dbTypes.length ? dbTypes : [{ db_type: "postgres", available: true, verified: true, default_port: 5432, notes: "" }]).map((d) =>
                <option key={d.db_type} value={d.db_type} disabled={!d.available}>{d.db_type}{!d.available ? " (not installed on this server)" : !d.verified ? " (not yet verified)" : ""}</option>)}
            </select></label>
          <Text label={f.db_type === "sqlite" ? "File path inside your business folder" : f.db_type === "oracle" ? "Service name" : "Database name"} value={f.database ?? ""} onChange={(v) => set("database", v)} />
          {f.db_type !== "sqlite" && <Text label="Host" value={f.host ?? ""} onChange={(v) => set("host", v)} required={f.db_type !== "odbc"} />}
          {f.db_type !== "sqlite" && <Text label="Port" type="number" value={f.port ?? ""} onChange={(v) => set("port", v)} placeholder={selected?.default_port ? String(selected.default_port) : ""} />}
          {f.db_type !== "sqlite" && <Text label="Username (read-only login)" value={f.username ?? ""} onChange={(v) => set("username", v)} />}
          {f.db_type !== "sqlite" && <Text label="Password" type="password" value={f.password ?? ""} onChange={(v) => set("password", v)} autoComplete="new-password" />}
          <label className="text-xs text-ink-muted sm:col-span-2">Query (one SELECT) *
            <textarea className={`${inputCls} mt-1 font-mono`} rows={3} required value={f.query ?? ""} onChange={(e) => set("query", e.target.value)} placeholder="SELECT id, sold_at, total, branch FROM sales WHERE sold_at > now() - interval '7 days'" /></label>
          {selected?.notes && <p className="text-xs text-ink-muted sm:col-span-2">{selected.notes}</p>}
        </div>
      )}
      {type === "sftp" && (
        <div className="grid sm:grid-cols-2 gap-2">
          <Text label="Host" value={f.host ?? ""} onChange={(v) => set("host", v)} required />
          <Text label="Port" type="number" value={f.port ?? ""} onChange={(v) => set("port", v)} placeholder="22" />
          <Text label="Username" value={f.username ?? ""} onChange={(v) => set("username", v)} required />
          <Text label="Password" type="password" value={f.password ?? ""} onChange={(v) => set("password", v)} required autoComplete="new-password" />
          <Text label="Remote folder" value={f.remote_directory ?? ""} onChange={(v) => set("remote_directory", v)} required placeholder="/exports" />
          <Text label="File name pattern" value={f.file_pattern ?? "*"} onChange={(v) => set("file_pattern", v)} placeholder="sales_*.csv" />
          <Text label="Server key fingerprint (recommended)" value={f.host_key_sha256 ?? ""} onChange={(v) => set("host_key_sha256", v)} />
        </div>
      )}
      {type === "email" && (
        <div className="grid sm:grid-cols-2 gap-2">
          <Text label="Mail server (IMAP)" value={f.imap_host ?? ""} onChange={(v) => set("imap_host", v)} required placeholder="imap.example.com" />
          <Text label="Port" type="number" value={f.port ?? ""} onChange={(v) => set("port", v)} placeholder="993" />
          <Text label="Username" value={f.username ?? ""} onChange={(v) => set("username", v)} required />
          <Text label="Password" type="password" value={f.password ?? ""} onChange={(v) => set("password", v)} required autoComplete="new-password" />
          <Text label="Mailbox" value={f.mailbox ?? "INBOX"} onChange={(v) => set("mailbox", v)} />
          <Text label="Only from addresses containing" value={f.from_filter ?? ""} onChange={(v) => set("from_filter", v)} />
          <Text label="Only subjects containing" value={f.subject_filter ?? ""} onChange={(v) => set("subject_filter", v)} />
        </div>
      )}
      {type === "shared_folder" && (
        <div className="grid sm:grid-cols-2 gap-2">
          <Text label="Folder (inside your business folder)" value={f.path ?? ""} onChange={(v) => set("path", v)} required placeholder="exports" />
          <Text label="File name pattern" value={f.file_pattern ?? "*"} onChange={(v) => set("file_pattern", v)} />
        </div>
      )}

      <MappingFields f={f} set={set} webhook={type === "webhook"} />

      <div className="grid sm:grid-cols-2 gap-2">
        <label className="text-xs text-ink-muted">How often should data arrive?
          <select className={`${inputCls} mt-1`} value={freq} onChange={(e) => setFreq(e.target.value)}>
            <option value="">Not sure</option><option value="realtime">Live</option><option value="near_realtime">Every few minutes</option><option value="periodic">Daily or so</option><option value="slow">Rarely</option>
          </select></label>
        {pull && <Text label="Check automatically every (minutes, 5–10080)" type="number" value={interval} onChange={setIntervalMin} placeholder="60" />}
      </div>
      <Notice error={err} />
      <button className={primaryCls} disabled={busy}>{busy ? "Connecting…" : "Connect source"}</button>
    </form>
  );
}

function ConnectorList({ tenantId, rows, isOwner, reload, setNotice, setError }: {
  tenantId: string; rows: ConnectorRow[]; isOwner: boolean; reload: () => void; setNotice: (s: string | null) => void; setError: (s: string | null) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadFor, setUploadFor] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  async function run(id: string, fn: () => Promise<unknown>, done: string) {
    setBusy(id); setError(null); setNotice(null);
    try { await fn(); setNotice(done); reload(); } catch (e) { setError(errText(e)); } finally { setBusy(null); }
  }
  const state = (c: ConnectorRow) => (!c.is_active ? ["Paused", "#8b93a1"] : c.last_sync_status === "failed" || c.last_sync_status === "error" ? ["Failing", "#e5484d"] : c.last_sync_at ? ["Working", "#4caf7d"] : ["Waiting for data", "#e8c547"]);
  return (
    <>
      <input ref={fileRef} type="file" hidden accept=".csv,.xlsx,.xls,.json,.xml,.txt"
        onChange={(e) => { const file = e.target.files?.[0]; const id = uploadFor; e.target.value = ""; if (file && id) run(id, () => connectorApi.upload(tenantId, id, file), `Uploaded ${file.name}.`); }} />
      <ul className="divide-y divide-border border border-border rounded-sm">
        {rows.map((c) => {
          const [label, color] = state(c);
          const pull = ["database", "sftp", "email", "shared_folder"].includes(c.connector_type);
          return (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-3">
              <div className="min-w-[180px]">
                <div className="text-sm font-medium">{c.name}</div>
                <div className="text-xs text-ink-muted">{c.connector_type.replace("_", " ")}
                  {c.sync_interval_minutes ? ` · every ${c.sync_interval_minutes} min` : ""}
                  {c.last_sync_at ? ` · last data ${new Date(c.last_sync_at + "Z").toLocaleString()}` : ""}</div>
              </div>
              <span className="text-xs flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} aria-hidden />{label}</span>
              <div className="flex gap-2">
                {c.connector_type === "file" && <button className={btnCls} disabled={busy === c.id} onClick={() => { setUploadFor(c.id); fileRef.current?.click(); }}>Upload file</button>}
                {pull && <button className={btnCls} disabled={busy === c.id} onClick={() => run(c.id, () => connectorApi.sync(tenantId, c.id, c.connector_type), "Sync finished.")}>{busy === c.id ? "Syncing…" : "Sync now"}</button>}
                {isOwner && <button className={btnCls} onClick={() => run(c.id, () => connectorApi.update(tenantId, c.id, { is_active: !c.is_active }), c.is_active ? "Paused." : "Resumed.")}>{c.is_active ? "Pause" : "Resume"}</button>}
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
      <div className="grid sm:grid-cols-3 gap-2">
        <label className="text-xs text-ink-muted">Type
          <select className={`${inputCls} mt-1`} value={entity} onChange={(e) => setEntity(e.target.value as "sale" | "expense")}><option value="sale">Sale</option><option value="expense">Expense</option></select></label>
        <Text label="Amount" type="number" required value={amount} onChange={setAmount} />
        <Text label="Quantity" type="number" value={f.quantity} onChange={set("quantity")} />
        <Text label="Location / branch" value={f.location} onChange={set("location")} />
        <Text label="Product" value={f.product} onChange={set("product")} />
        <Text label={entity === "sale" ? "Customer" : "Supplier"} value={entity === "sale" ? f.customer : f.supplier} onChange={set(entity === "sale" ? "customer" : "supplier")} />
      </div>
      <Text label="Note" value={f.note} onChange={set("note")} />
      <Notice error={err} notice={ok} />
      <button className={primaryCls} disabled={busy || !amount}>{busy ? "Saving…" : "Save entry"}</button>
    </form>
  );
}

export function DataPage() {
  const t = useT();
  const isOwner = getClaims().role === "owner";
  return <Shell>{(tenant) => <DataBody tenantId={tenant.id} isOwner={isOwner} title={t("data.title")} />}</Shell>;
}

function DataBody({ tenantId, isOwner, title }: { tenantId: string; isOwner: boolean; title: string }) {
  const t = useT();
  const [rows, setRows] = useState<ConnectorRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<ConnectorRow | null>(null);
  const load = useCallback(() => connectorApi.list(tenantId).then(setRows).catch((e) => setErr(errText(e))), [tenantId]);
  useEffect(() => { load(); }, [load]);
  const base = (import.meta.env.VITE_API_BASE ?? "http://localhost:8000") as string;
  return (
    <>
      <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
      <Notice error={err} notice={notice} />
      {created?.webhook_url && (
        <Card accent="#e8c547">
          <p className="text-sm font-medium">Copy this link now — it is shown only once.</p>
          <code className="block mt-2 text-xs break-all bg-ink border border-border rounded-sm p-2 select-all">{base}{created.webhook_url}</code>
          <p className="text-xs text-ink-muted mt-2">Have your system POST a JSON record to it for every sale or expense.</p>
          <button className={`${btnCls} mt-2`} onClick={() => setCreated(null)}>I've saved it</button>
        </Card>
      )}
      <Section title="Connected sources" action={isOwner && !adding ? <button className={primaryCls} onClick={() => setAdding(true)}>{t("data.add")}</button> : undefined}>
        {rows.length === 0 && !adding ? <Empty>No data source connected yet. Connect one, or add entries by hand below.</Empty> : <ConnectorList tenantId={tenantId} rows={rows} isOwner={isOwner} reload={load} setNotice={setNotice} setError={setErr} />}
        {adding && isOwner && (
          <Card>
            <AddSource tenantId={tenantId} onDone={(c) => { setAdding(false); setCreated(c); setNotice(`Connected ${c.name}.`); load(); }} />
            <button className={`${btnCls} mt-3`} onClick={() => setAdding(false)}>{t("common.cancel")}</button>
          </Card>
        )}
      </Section>
      <Section title={t("data.capture")} hint="For a business with no system yet: one-off sales and expenses. Retrying a save never double-counts.">
        <Card><CaptureForm tenantId={tenantId} /></Card>
      </Section>
    </>
  );
}
