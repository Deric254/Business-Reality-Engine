import { useCallback, useEffect, useState } from "react";
import { systemApi, type FoundDatabase, type ReadingField, type ReadingTable, type SystemDiscovery, type SystemLink, type SystemProposal } from "../api/client";
import { errText } from "../lib/errors";
import { btnCls, inputCls, primaryCls } from "./ui";

const FIELD_LABEL: Record<ReadingField, string> = { id: "Unique number", date: "Date", amount: "Amount", instant: "Exact time", customer: "Customer", product: "Product", location: "Branch", quantity: "Quantity", supplier: "Supplier", label: "Name to show" };
const MONEY_ROLES = ["sale", "expense", "refund"];
const ALL_FIELDS = Object.keys(FIELD_LABEL) as ReadingField[];

const shortName = (p: string) => (p.includes("://") ? p : p.split(/[\\/]/).slice(-2).join("/"));

/** "Your own system": the databases the helper app found on the computer, what we understood about each, and a place to correct
 *  that reading before anything is read. Only names are shown here; no row of the person's data has left their computer. */
export function SystemReview({ tenantId, link, reload, setError }: { tenantId: string; link: SystemLink; reload: () => void; setError: (s: string | null) => void }) {
  const id = link.connector_id!;
  const [disc, setDisc] = useState<SystemDiscovery | null>(null);
  const [path, setPath] = useState<string | null>(null);
  const [draft, setDraft] = useState<SystemProposal | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => systemApi.discovery(tenantId, id).then(setDisc).catch((e) => setError(errText(e))), [tenantId, id, setError]);
  useEffect(() => { void load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);

  const db: FoundDatabase | undefined = disc?.found.find((d) => d.path === path);
  const usable = (disc?.found ?? []).filter((d) => !d.error);
  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }
  const choose = (p: string) => run(async () => { setPath(p); setDraft(await systemApi.propose(tenantId, id, p)); });
  const setRow = (i: number, patch: Partial<ReadingTable>) => setDraft((d) => d && { ...d, tables: d.tables.map((t, j) => (j === i ? { ...t, ...patch } : t)) });
  const roles = disc?.roles ?? {};
  const mainFields = (role: string): ReadingField[] => [...new Set<ReadingField>(["id", ...(roles[role]?.required ?? []), ...(MONEY_ROLES.includes(role) ? (["date", "amount"] as ReadingField[]) : [])])];
  const complete = !!draft && draft.tables.length > 0 && draft.tables.every((t) => (roles[t.role]?.required ?? []).every((f) => t[f]));
  const confirm = () => run(async () => {
    await systemApi.confirmReading(tenantId, id, { path: path!, tables: draft!.tables, business: draft!.business, currency: draft!.currency });
    setDraft(null); reload();
  });

  if (!disc) return <p className="text-sm text-ink-muted">Loading what was found…</p>;
  return (
    <div className="space-y-3 text-sm" aria-label="Review what was found">
      {usable.length === 0 && <p className="text-ink-muted">{disc.scan_requested ? "Looking on the computer now…" : "No readable database found yet."}</p>}
      {usable.length > 0 && (
        <fieldset className="space-y-1"><legend className="font-medium mb-1">Which one holds your business?</legend>
          {usable.map((d) => (
            <label key={d.path} className="flex items-start gap-2">
              <input type="radio" name={`db-${id}`} className="mt-1" checked={path === d.path} disabled={busy} onChange={() => choose(d.path)} />
              <span><span className="break-all">{shortName(d.path)}</span> <span className="text-xs text-ink-muted">{d.tables.length} tables, {d.tables.reduce((n, t) => n + (t.rows ?? 0), 0).toLocaleString("en-US")} rows</span></span>
            </label>
          ))}
        </fieldset>
      )}
      <button className={btnCls} disabled={busy || disc.scan_requested} onClick={() => run(async () => { await systemApi.scan(tenantId, id); await load(); })}>{disc.scan_requested ? "Looking…" : "Look again"}</button>
      {draft && db && (
        <div className="space-y-3 border border-field rounded-sm p-3">
          <p className="text-xs text-ink-muted">{draft.used_ai ? "Read with AI from the table and column names only." : "Read from the table and column names alone. Add an AI key in Settings for a fuller reading."} Please correct anything that is wrong.</p>
          <label className="block">What kind of business is this?
            <input className={inputCls} value={draft.business.kind} maxLength={80} onChange={(e) => setDraft({ ...draft, business: { ...draft.business, kind: e.target.value } })} /></label>
          <label className="block">What it keeps track of
            <textarea className={inputCls} rows={2} maxLength={600} value={draft.business.description} onChange={(e) => setDraft({ ...draft, business: { ...draft.business, description: e.target.value } })} /></label>
          {draft.tables.map((t, i) => {
            const cols = db.tables.find((x) => x.name === t.table)?.columns ?? [];
            const pick = (k: keyof ReadingTable, label: string) => (
              <label key={k} className="block text-xs">{label}
                <select className={inputCls} value={(t[k] as string | null | undefined) ?? ""} onChange={(e) => setRow(i, { [k]: e.target.value || null })}>
                  <option value="">{k === "id" ? "Use the row's position" : "None"}</option>{cols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select></label>
            );
            return (
              <div key={t.table} className="space-y-2 border-t border-field pt-2">
                <div className="flex flex-wrap items-center gap-2"><strong>{t.table}</strong>
                  <select className={inputCls + " max-w-xs"} aria-label={`What ${t.table} holds`} value={t.role} onChange={(e) => setRow(i, { role: e.target.value as ReadingTable["role"] })}>
                    {Object.entries(roles).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
                  <button className={btnCls} onClick={() => setDraft({ ...draft, tables: draft.tables.filter((_, j) => j !== i) })}>Don't use</button></div>
                {t.reason && <p className="text-xs text-ink-muted">{t.reason}</p>}
                <div className="grid sm:grid-cols-3 gap-2">{mainFields(t.role).map((k) => pick(k, k === "amount" ? roles[t.role]?.amount_label ?? FIELD_LABEL.amount : FIELD_LABEL[k]))}</div>
                <details><summary className="text-xs text-ink-muted cursor-pointer">More details</summary><div className="grid sm:grid-cols-3 gap-2 mt-2">{ALL_FIELDS.filter((k) => !mainFields(t.role).includes(k)).map((k) => pick(k, k === "amount" ? roles[t.role]?.amount_label ?? FIELD_LABEL.amount : FIELD_LABEL[k]))}</div></details>
              </div>
            );
          })}
          {db.tables.some((x) => !draft.tables.some((t) => t.table === x.name)) && (
            <select className={inputCls + " max-w-xs"} aria-label="Add another table" value="" onChange={(e) => e.target.value && setDraft({ ...draft, tables: [...draft.tables, { table: e.target.value, role: "sale" }] })}>
              <option value="">Add another table…</option>{db.tables.filter((x) => !draft.tables.some((t) => t.table === x.name)).map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}</select>
          )}
          {draft.notes.length > 0 && <ul className="text-xs text-ink-muted list-disc pl-4">{draft.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          {draft.tables.length === 0 && <p className="text-xs text-ink-muted">Nothing here looked like sales, expenses or refunds. Add the table that records your money, or look at another database.</p>}
          <button className={primaryCls} disabled={busy || !complete} onClick={confirm}>{complete ? "This is right. Start reading" : "Each table needs its key columns filled in"}</button>
        </div>
      )}
    </div>
  );
}
