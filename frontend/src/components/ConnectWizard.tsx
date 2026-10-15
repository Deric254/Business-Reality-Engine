import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, connectorApi, type ConnectorRow, type DbType, type Inspection } from "../api/client";
import { API_BASE } from "../api/client";
import { Card, Notice, btnCls, inputCls, primaryCls } from "./ui";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");

type Kind = "sheets" | "file" | "woo" | "shopify" | "link" | "webhook" | "database" | "agent" | "sftp" | "email" | "folder";
interface Entry { id: Kind; title: string; blurb: string; type: string; group: string; note?: string }

const CATALOG: Entry[] = [
  { id: "sheets", title: "Google Sheets", blurb: "Paste a share link. Updates are picked up on a schedule.", type: "url", group: "Start here" },
  { id: "file", title: "Excel or CSV file", blurb: "Upload an export from your till, accounting or any spreadsheet.", type: "file", group: "Start here" },
  { id: "woo", title: "WooCommerce", blurb: "Online shop orders, pulled automatically.", type: "url", group: "Your shop or system", note: "Ready-made setup; not yet tried against a live store, so use Test first." },
  { id: "shopify", title: "Shopify", blurb: "Orders from your Shopify store.", type: "url", group: "Your shop or system", note: "Ready-made setup; not yet tried against a live store, so use Test first." },
  { id: "link", title: "Web link or API", blurb: "Any web address that returns CSV, Excel or JSON, with an optional key or login.", type: "url", group: "Your shop or system" },
  { id: "webhook", title: "Live feed (webhook)", blurb: "Your system sends each sale or expense to a private link the moment it happens.", type: "webhook", group: "Your shop or system" },
  { id: "database", title: "Database", blurb: "Read-only query against PostgreSQL, MySQL, SQL Server, Oracle or ODBC.", type: "database", group: "Your shop or system" },
  { id: "sftp", title: "SFTP folder", blurb: "Pick up exported files from an SFTP server.", type: "sftp", group: "Advanced" },
  { id: "email", title: "Email attachments", blurb: "Read reports your system emails to a mailbox.", type: "email", group: "Advanced" },
  { id: "folder", title: "Shared folder", blurb: "Files dropped into your business folder on the server.", type: "shared_folder", group: "Advanced" },
  { id: "agent", title: "Local agent", blurb: "A small program on a computer in your shop that sends data out. Nothing to open on your firewall.", type: "local_agent", group: "Advanced" },
];

type F = Record<string, string>;

function Field({ label, value, onChange, type = "text", required, placeholder, help, auto }: { label: string; value: string; onChange: (v: string) => void; type?: string; required?: boolean; placeholder?: string; help?: string; auto?: string }) {
  return (
    <label className="block text-sm text-ink-muted">{label}{required && " *"}
      <input className={`${inputCls} mt-1`} type={type} value={value} required={required} placeholder={placeholder} autoComplete={auto ?? "off"} onChange={(e) => onChange(e.target.value)} />
      {help && <span className="block text-xs text-ink-faint mt-1">{help}</span>}
    </label>
  );
}

const MAP_FIELDS: { key: string; label: string; required?: boolean; help?: string }[] = [
  { key: "timestamp", label: "When it happened", required: true },
  { key: "amount", label: "Amount" },
  { key: "quantity", label: "Quantity" },
  { key: "currency", label: "Currency" },
  { key: "location", label: "Branch or location" },
  { key: "customer_id", label: "Customer" },
  { key: "product_id", label: "Product" },
  { key: "entity_id", label: "Unique record number", help: "Receipt or order number. If you leave it empty, repeated rows are still recognised so nothing counts twice." },
];

function Mapper({ f, set, info, webhook }: { f: F; set: (k: string, v: string) => void; info: Inspection | null; webhook: boolean }) {
  const suffix = webhook ? "_path" : "_col";
  const cols = info?.columns;
  return (
    <fieldset className="space-y-3">
      <legend className="font-semibold">{cols ? "Check what each column means" : webhook ? "Where to find each value in what you send" : "What do your columns mean?"}</legend>
      {cols && <p className="text-sm text-ink-muted">We matched these from your column names. Change any that are wrong.</p>}
      {webhook && <p className="text-sm text-ink-muted">Use the path into your JSON, for example <code className="bg-ink px-1 rounded">data.amount</code>.</p>}
      <div className="grid sm:grid-cols-2 gap-3">
        <label className="block text-sm text-ink-muted">Each row is a
          <select className={`${inputCls} mt-1`} value={f.entity} onChange={(e) => { set("entity", e.target.value); set("event_type", `${e.target.value}.created`); }}>
            <option value="sale">Sale (money in)</option><option value="expense">Expense (money out)</option>
          </select></label>
        <Field label="Currency, if the data has none" value={f.default_currency} onChange={(v) => set("default_currency", v.toUpperCase())} />
        {MAP_FIELDS.map((m) => {
          const k = m.key === "timestamp" ? `timestamp${suffix}` : `${m.key}${suffix}`;
          return (
            <label key={k} className="block text-sm text-ink-muted">{m.label}{m.required && !webhook ? " *" : ""}
              {cols ? (
                <select className={`${inputCls} mt-1`} value={f[k] ?? ""} onChange={(e) => set(k, e.target.value)} required={m.required}>
                  <option value="">{m.required ? "Choose a column" : "Not in my data"}</option>
                  {cols.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : <input className={`${inputCls} mt-1`} value={f[k] ?? ""} onChange={(e) => set(k, e.target.value)} required={m.required && !webhook} placeholder={webhook ? "data.amount" : "Column name"} />}
              {m.help && <span className="block text-xs text-ink-faint mt-1">{m.help}</span>}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function Preview({ info }: { info: Inspection }) {
  return (
    <div className="space-y-2">
      <div className="text-sm text-ink-muted">Found <span className="font-semibold text-ink-text">{info.row_count.toLocaleString()} rows</span> and {info.columns.length} columns. First rows:</div>
      <div className="overflow-x-auto border border-border rounded-sm">
        <table className="text-xs w-full">
          <thead className="bg-panel-raised"><tr>{info.columns.slice(0, 8).map((c) => <th key={c} className="text-left font-medium px-2.5 py-1.5 whitespace-nowrap">{c}</th>)}</tr></thead>
          <tbody>{info.sample.slice(0, 3).map((r, i) => <tr key={i} className="border-t border-border">{info.columns.slice(0, 8).map((c) => <td key={c} className="px-2.5 py-1.5 whitespace-nowrap max-w-[14rem] truncate">{String(r[c] ?? "")}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

const INTERVALS = [{ v: "", l: "Only when I click Sync" }, { v: "15", l: "Every 15 minutes" }, { v: "60", l: "Every hour" }, { v: "360", l: "Every 6 hours" }, { v: "1440", l: "Once a day" }];

export function ConnectWizard({ tenantId, onClose, onDone }: { tenantId: string; onClose: () => void; onDone: (created: ConnectorRow, summary: string | null) => void }) {
  const [kind, setKind] = useState<Kind | null>(null);
  const [f, setF] = useState<F>({ entity: "sale", event_type: "sale.created", default_currency: "KES", db_type: "postgres", mailbox: "INBOX", file_pattern: "*", use_ssl: "true", auth_type: "none" });
  const [name, setName] = useState("");
  const [interval, setIntervalMin] = useState("");
  const [info, setInfo] = useState<Inspection | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dbTypes, setDbTypes] = useState<DbType[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));
  const entry = CATALOG.find((c) => c.id === kind) ?? null;

  useEffect(() => { if (kind === "database") connectorApi.dbTypes(tenantId).then(setDbTypes).catch(() => setDbTypes([])); }, [kind, tenantId]);

  function pick(k: Kind) {
    setKind(k); setErr(null); setInfo(null); setFile(null);
    setName(CATALOG.find((c) => c.id === k)?.title ?? "");
    if (k === "woo") setF((p) => ({ ...p, auth_type: "basic", paginate: "page", page_size: "100", timestamp_col: "date_created", amount_col: "total", currency_col: "currency", entity_id_col: "id", customer_id_col: "billing.email" }));
    if (k === "shopify") setF((p) => ({ ...p, auth_type: "header", auth_header_name: "X-Shopify-Access-Token", json_path: "orders", timestamp_col: "created_at", amount_col: "total_price", currency_col: "currency", entity_id_col: "id", customer_id_col: "customer.email" }));
  }

  /** The config pieces that are about reaching the source (not the column mapping). */
  function sourceConfig(): Record<string, unknown> {
    const num = (k: string) => (f[k] ? Number(f[k]) : undefined);
    if (kind === "sheets" || kind === "link") return { url: f.url, auth_type: f.auth_type || "none", ...(f.auth_type === "bearer" || f.auth_type === "header" ? { password: f.secret } : {}), ...(f.auth_type === "header" ? { auth_header_name: f.auth_header_name } : {}), ...(f.auth_type === "basic" ? { username: f.username, password: f.secret } : {}), ...(f.json_path ? { json_path: f.json_path } : {}), ...(f.paginate === "page" ? { paginate: "page" } : {}) };
    if (kind === "woo") return { url: `${(f.store ?? "").replace(/\/+$/, "")}/wp-json/wc/v3/orders?status=completed`, auth_type: "basic", username: f.username, password: f.secret, paginate: "page", page_size: 100 };
    if (kind === "shopify") return { url: `https://${(f.store ?? "").replace(/^https?:\/\//, "").replace(/\/+$/, "")}/admin/api/2024-10/orders.json?status=any&limit=250`, auth_type: "header", auth_header_name: "X-Shopify-Access-Token", password: f.secret, json_path: "orders" };
    if (kind === "database") return { db_type: f.db_type, host: f.host ?? "", port: num("port") ?? null, database: f.database ?? "", username: f.username ?? "", password: f.secret ?? "", query: f.query ?? "" };
    if (kind === "sftp") return { host: f.host, port: num("port") ?? 22, username: f.username, password: f.secret, remote_directory: f.remote_directory, file_pattern: f.file_pattern || "*", ...(f.host_key_sha256 ? { host_key_sha256: f.host_key_sha256 } : {}) };
    if (kind === "email") return { imap_host: f.imap_host, imap_port: num("port") ?? 993, use_ssl: f.use_ssl !== "false", username: f.username, password: f.secret, mailbox: f.mailbox || "INBOX", ...(f.from_filter ? { from_filter: f.from_filter } : {}), ...(f.subject_filter ? { subject_filter: f.subject_filter } : {}) };
    if (kind === "folder") return { path: f.path ?? "", file_pattern: f.file_pattern || "*" };
    return {};
  }
  const mappingConfig = () => {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(f)) if (/(_col|_path)$/.test(k) || k === "entity" || k === "event_type" || k === "default_currency") if (f[k]) out[k] = f[k];
    return out;
  };

  const canInspect = kind === "sheets" || kind === "link" || kind === "woo" || kind === "shopify" || kind === "file";
  const mapped = !canInspect || info != null;

  async function inspect() {
    setBusy(true); setErr(null);
    try {
      let res: Inspection;
      if (kind === "file") { if (!file) throw new Error("Choose a file first."); res = await connectorApi.inspectFile(tenantId, file); }
      else res = await connectorApi.inspectUrl(tenantId, sourceConfig());
      setInfo(res);
      // Presets already know their columns; for everything else take the detected suggestion.
      const sug = res.suggested_mapping;
      setF((p) => {
        const next = { ...p };
        for (const [k, v] of Object.entries(sug)) if (kind !== "woo" && kind !== "shopify" || !p[k]) next[k] = v;
        return next;
      });
    } catch (x) { setErr(errText(x)); setInfo(null); } finally { setBusy(false); }
  }

  async function connect(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const pull = ["url", "database", "sftp", "email", "shared_folder"].includes(entry!.type);
      const config = { ...sourceConfig(), ...(entry!.type === "webhook" || entry!.type === "local_agent" ? {} : {}), ...mappingConfig() };
      const created = await connectorApi.create({
        tenant_id: tenantId, name: name.trim() || entry!.title, connector_type: entry!.type, config,
        expected_frequency: entry!.type === "webhook" ? "realtime" : interval ? "periodic" : null,
        sync_interval_minutes: pull && interval ? Number(interval) : null,
      });
      let summary: string | null = null;
      if (kind === "file" && file) {
        const r = await connectorApi.upload(tenantId, created.id, file) as { rows_processed?: number; failed?: number };
        summary = `Imported ${r.rows_processed ?? 0} rows${r.failed ? `, ${r.failed} had problems` : ""}.`;
      } else if (entry!.type === "url") {
        const r = await connectorApi.sync(tenantId, created.id, "url") as { new_rows?: number; failed?: number };
        summary = `Imported ${r.new_rows ?? 0} rows${r.failed ? `, ${r.failed} had problems` : ""}.`;
      }
      onDone(created, summary);
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }

  // ---------- step 1: pick ----------
  if (!entry) {
    const groups = [...new Set(CATALOG.map((c) => c.group))];
    return (
      <Card className="!p-6 space-y-6">
        <div className="flex items-start justify-between gap-3">
          <div><h2 className="display text-2xl">Where does your data live?</h2><p className="text-sm text-ink-muted mt-1">Pick the closest match. You can connect more than one.</p></div>
          <button className={btnCls} onClick={onClose}>Cancel</button>
        </div>
        {groups.map((g) => (
          <div key={g} className="space-y-2">
            <h3 className="font-semibold text-ink-muted text-sm">{g}</h3>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {CATALOG.filter((c) => c.group === g).map((c) => (
                <button key={c.id} onClick={() => pick(c.id)} className="text-left border border-border rounded-xl p-4 bg-panel hover:border-info hover:bg-info-soft/40 transition-colors">
                  <div className="font-semibold">{c.title}</div>
                  <div className="text-sm text-ink-muted mt-1">{c.blurb}</div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </Card>
    );
  }

  // ---------- step 2+: details, map, finish ----------
  const urlish = entry.type === "url";
  const pull = ["url", "database", "sftp", "email", "shared_folder"].includes(entry.type);
  const needsMapping = entry.type !== "manual";
  const savedAfterInspect = !canInspect || info != null;
  const sheetsHelp = kind === "sheets" && <p className="text-sm text-ink-muted">In Google Sheets choose <strong>Share</strong>, set General access to <strong>Anyone with the link: Viewer</strong>, then copy the link. Only that sheet is read, never your other files.</p>;

  return (
    <Card className="!p-6 space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div><h2 className="display text-2xl">Connect {entry.title}</h2>{entry.note && <p className="text-sm text-ink-muted mt-1">{entry.note}</p>}</div>
        <div className="flex gap-2"><button className={btnCls} onClick={() => { setKind(null); setInfo(null); }}>Back</button><button className={btnCls} onClick={onClose}>Cancel</button></div>
      </div>

      <form onSubmit={connect} className="space-y-6">
        {/* connection details */}
        <div className="space-y-3">
          {sheetsHelp}
          {(kind === "sheets" || kind === "link") && (
            <>
              <Field label={kind === "sheets" ? "Sheet link" : "Web address"} value={f.url ?? ""} onChange={(v) => { set("url", v); setInfo(null); }} required placeholder={kind === "sheets" ? "https://docs.google.com/spreadsheets/d/…" : "https://example.com/sales.csv"} />
              {kind === "link" && (
                <div className="grid sm:grid-cols-2 gap-3">
                  <label className="block text-sm text-ink-muted">Access
                    <select className={`${inputCls} mt-1`} value={f.auth_type} onChange={(e) => { set("auth_type", e.target.value); setInfo(null); }}>
                      <option value="none">Open link, no login</option><option value="bearer">API key (Bearer token)</option><option value="header">API key in a named header</option><option value="basic">Username and password</option>
                    </select></label>
                  {f.auth_type === "header" && <Field label="Header name" value={f.auth_header_name ?? ""} onChange={(v) => set("auth_header_name", v)} placeholder="X-Api-Key" required />}
                  {f.auth_type === "basic" && <Field label="Username" value={f.username ?? ""} onChange={(v) => set("username", v)} required />}
                  {f.auth_type !== "none" && <Field label={f.auth_type === "basic" ? "Password" : "Key"} type="password" value={f.secret ?? ""} onChange={(v) => set("secret", v)} required auto="new-password" />}
                  <Field label="Where the list is in the response (JSON only)" value={f.json_path ?? ""} onChange={(v) => set("json_path", v)} placeholder="orders" help="Leave empty if the response is the list itself." />
                  <label className="block text-sm text-ink-muted">Paging
                    <select className={`${inputCls} mt-1`} value={f.paginate ?? ""} onChange={(e) => set("paginate", e.target.value)}><option value="">One request</option><option value="page">Keep fetching pages (?page=1, 2, 3…)</option></select></label>
                </div>
              )}
            </>
          )}
          {kind === "woo" && (<div className="grid sm:grid-cols-2 gap-3">
            <Field label="Store address" value={f.store ?? ""} onChange={(v) => { set("store", v); setInfo(null); }} required placeholder="https://myshop.co.ke" />
            <div />
            <Field label="Consumer key" value={f.username ?? ""} onChange={(v) => { set("username", v); setInfo(null); }} required help="WooCommerce, Settings, Advanced, REST API, Add key (Read access)." />
            <Field label="Consumer secret" type="password" value={f.secret ?? ""} onChange={(v) => { set("secret", v); setInfo(null); }} required auto="new-password" />
          </div>)}
          {kind === "shopify" && (<div className="grid sm:grid-cols-2 gap-3">
            <Field label="Store domain" value={f.store ?? ""} onChange={(v) => { set("store", v); setInfo(null); }} required placeholder="myshop.myshopify.com" />
            <Field label="Admin API access token" type="password" value={f.secret ?? ""} onChange={(v) => { set("secret", v); setInfo(null); }} required auto="new-password" help="Create a custom app with read_orders access." />
          </div>)}
          {kind === "file" && (
            <div className="space-y-2">
              <input ref={fileRef} type="file" hidden accept=".csv,.xlsx,.json,.xml,.txt,.tsv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setInfo(null); }} />
              <button type="button" className={btnCls} onClick={() => fileRef.current?.click()}>{file ? "Choose a different file" : "Choose a file"}</button>
              {file && <span className="text-sm ml-3">{file.name}</span>}
              <p className="text-xs text-ink-faint">CSV, Excel, JSON, XML or text, up to 10 MB. You can upload newer files to this source later; rows already imported are skipped.</p>
            </div>
          )}
          {kind === "database" && (
            <div className="grid sm:grid-cols-2 gap-3">
              <label className="block text-sm text-ink-muted">Database type
                <select className={`${inputCls} mt-1`} value={f.db_type} onChange={(e) => set("db_type", e.target.value)}>
                  {(dbTypes.length ? dbTypes : [{ db_type: "postgres", available: true, verified: true, default_port: 5432, notes: "" }]).map((d) => <option key={d.db_type} value={d.db_type} disabled={!d.available}>{d.db_type}{!d.available ? " (not installed here)" : !d.verified ? " (not yet verified)" : ""}</option>)}
                </select></label>
              <Field label={f.db_type === "sqlite" ? "File path in your business folder" : f.db_type === "oracle" ? "Service name" : "Database name"} value={f.database ?? ""} onChange={(v) => set("database", v)} />
              {f.db_type !== "sqlite" && <Field label="Host" value={f.host ?? ""} onChange={(v) => set("host", v)} required={f.db_type !== "odbc"} />}
              {f.db_type !== "sqlite" && <Field label="Port" type="number" value={f.port ?? ""} onChange={(v) => set("port", v)} placeholder={String(dbTypes.find((d) => d.db_type === f.db_type)?.default_port ?? "")} />}
              {f.db_type !== "sqlite" && <Field label="Username (a read-only login)" value={f.username ?? ""} onChange={(v) => set("username", v)} />}
              {f.db_type !== "sqlite" && <Field label="Password" type="password" value={f.secret ?? ""} onChange={(v) => set("secret", v)} auto="new-password" />}
              <label className="block text-sm text-ink-muted sm:col-span-2">Query: one SELECT that returns your rows *
                <textarea className={`${inputCls} mt-1 font-mono`} rows={3} required value={f.query ?? ""} onChange={(e) => set("query", e.target.value)} placeholder="SELECT id, sold_at, total, branch FROM sales WHERE sold_at > now() - interval '7 days'" /></label>
              <p className="text-xs text-ink-faint sm:col-span-2">Passwords are encrypted. Only read-only queries are allowed, and we never write to your database. Use column names from your query in the next section.</p>
            </div>
          )}
          {kind === "sftp" && (<div className="grid sm:grid-cols-2 gap-3">
            <Field label="Host" value={f.host ?? ""} onChange={(v) => set("host", v)} required /><Field label="Port" type="number" value={f.port ?? ""} onChange={(v) => set("port", v)} placeholder="22" />
            <Field label="Username" value={f.username ?? ""} onChange={(v) => set("username", v)} required /><Field label="Password" type="password" value={f.secret ?? ""} onChange={(v) => set("secret", v)} required auto="new-password" />
            <Field label="Remote folder" value={f.remote_directory ?? ""} onChange={(v) => set("remote_directory", v)} required placeholder="/exports" /><Field label="File name pattern" value={f.file_pattern ?? "*"} onChange={(v) => set("file_pattern", v)} placeholder="sales_*.csv" />
            <Field label="Server key fingerprint (recommended)" value={f.host_key_sha256 ?? ""} onChange={(v) => set("host_key_sha256", v)} />
          </div>)}
          {kind === "email" && (<div className="grid sm:grid-cols-2 gap-3">
            <Field label="Mail server (IMAP)" value={f.imap_host ?? ""} onChange={(v) => set("imap_host", v)} required placeholder="imap.example.com" /><Field label="Port" type="number" value={f.port ?? ""} onChange={(v) => set("port", v)} placeholder="993" />
            <Field label="Username" value={f.username ?? ""} onChange={(v) => set("username", v)} required /><Field label="Password" type="password" value={f.secret ?? ""} onChange={(v) => set("secret", v)} required auto="new-password" />
            <Field label="Mailbox" value={f.mailbox ?? "INBOX"} onChange={(v) => set("mailbox", v)} /><Field label="Only from addresses containing" value={f.from_filter ?? ""} onChange={(v) => set("from_filter", v)} />
            <Field label="Only subjects containing" value={f.subject_filter ?? ""} onChange={(v) => set("subject_filter", v)} />
          </div>)}
          {kind === "folder" && (<div className="grid sm:grid-cols-2 gap-3">
            <Field label="Folder inside your business folder" value={f.path ?? ""} onChange={(v) => set("path", v)} required placeholder="exports" /><Field label="File name pattern" value={f.file_pattern ?? "*"} onChange={(v) => set("file_pattern", v)} />
          </div>)}
          {kind === "agent" && <p className="text-sm text-ink-muted">After you connect, you'll get a one-time token. Copy <code className="bg-ink px-1 rounded">agent/reality_agent.py</code> from the project to the shop computer and follow <code className="bg-ink px-1 rounded">agent/README.md</code>. Say below which field in each row holds the amount and the time.</p>}

          {canInspect && (
            <div className="flex items-center gap-3 flex-wrap">
              <button type="button" className={primaryCls} disabled={busy || (kind === "file" && !file)} onClick={inspect}>{busy && !info ? "Checking…" : info ? "Check again" : urlish ? "Test and detect columns" : "Read the file and detect columns"}</button>
              {info && <span className="text-sm text-opportunity font-medium">Connected. We can read your data.</span>}
            </div>
          )}
        </div>

        {info && <Preview info={info} />}
        {needsMapping && savedAfterInspect && <Mapper f={f} set={set} info={info} webhook={entry.type === "webhook" || entry.type === "local_agent"} />}

        {mapped && (
          <div className="grid sm:grid-cols-2 gap-3 border-t border-border pt-5">
            <Field label="Name this source" value={name} onChange={setName} required placeholder="Main till" />
            {pull && <label className="block text-sm text-ink-muted">Check for new data
              <select className={`${inputCls} mt-1`} value={interval} onChange={(e) => setIntervalMin(e.target.value)}>{INTERVALS.map((i) => <option key={i.v} value={i.v}>{i.l}</option>)}</select></label>}
          </div>
        )}
        <Notice error={err} />
        {mapped && <button className={primaryCls} disabled={busy}>{busy ? "Connecting…" : kind === "file" || urlish ? "Connect and import" : "Connect source"}</button>}
      </form>
    </Card>
  );
}

export function SecretOnce({ created, onClose }: { created: ConnectorRow; onClose: () => void }): ReactNode {
  const link = created.webhook_url ? `${API_BASE || window.location.origin}${created.webhook_url}` : null;
  return (
    <Card accent="#d4a017">
      <p className="font-semibold">{created.connector_type === "local_agent" ? "Copy this token now. It is shown only once." : "Copy this link now. It is shown only once."}</p>
      <code className="block mt-2 text-xs break-all bg-panel border border-field rounded-sm p-2 select-all">{created.connector_type === "local_agent" ? created.webhook_token : link}</code>
      <p className="text-sm text-ink-muted mt-2">{created.connector_type === "local_agent" ? "Put it in the agent's config.json." : "Have your system POST a JSON record to it for every sale or expense."}</p>
      <div className="mt-3 flex gap-2"><button className={btnCls} onClick={onClose}>I've saved it</button><Link to="/" className={btnCls}>Go to overview</Link></div>
    </Card>
  );
}
