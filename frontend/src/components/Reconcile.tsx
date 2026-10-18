import { useCallback, useEffect, useState } from "react";
import { ApiError, connectorApi, type ConnectorRow, type MergedPair, type OnlyHere, type OverlapSuggestion } from "../api/client";
import { Card, btnCls, primaryCls } from "./ui";

const DISMISSED = "bre.keepSeparate";
const key = (s: OverlapSuggestion) => `${s.keep.id}:${s.other.id}:${s.entity}`;
const kind = (entity: string) => (entity === "sale" ? "sales" : "expenses");
const money = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });

function readDismissed(): string[] {
  try { return JSON.parse(localStorage.getItem(DISMISSED) ?? "[]"); } catch { return []; }
}

function OnlyIn({ name, here }: { name: string; here: OnlyHere }) {
  if (!here.count) return null;
  return (
    <details className="text-sm">
      <summary className="cursor-pointer">Only in {name}: {here.count.toLocaleString()} record{here.count === 1 ? "" : "s"}, {money(here.amount)} in total</summary>
      <ul className="mt-1 text-ink-muted">
        {here.recent.map((r) => <li key={r.timestamp + r.amount}>{new Date(r.timestamp).toLocaleString()}: {money(r.amount)}</li>)}
      </ul>
    </details>
  );
}

/** Suggests counting two overlapping sources once, and shows what exists in only one of them after a merge. */
export function Reconcile({ tenantId, isOwner, rows, onChanged, setNotice, setError }: {
  tenantId: string; isOwner: boolean; rows: ConnectorRow[]; onChanged: () => void;
  setNotice: (m: string | null) => void; setError: (m: string | null) => void;
}) {
  const [suggestions, setSuggestions] = useState<OverlapSuggestion[]>([]);
  const [merged, setMerged] = useState<MergedPair[]>([]);
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => connectorApi.overlaps(tenantId).then((r) => { setSuggestions(r.suggestions); setMerged(r.merged); }).catch(() => undefined), [tenantId]);
  useEffect(() => { if (rows.length > 1) load(); }, [rows, load]);

  async function act(task: () => Promise<string>) {
    setBusy(true); setError(null);
    try { setNotice(await task()); onChanged(); } catch (e) { setError(e instanceof ApiError ? e.message : "Something went wrong. Try again."); } finally { setBusy(false); }
  }
  function keepSeparate(s: OverlapSuggestion) {
    const next = [...dismissed, key(s)];
    setDismissed(next);
    try { localStorage.setItem(DISMISSED, JSON.stringify(next)); } catch { /* the choice just will not be remembered */ }
  }

  const open = suggestions.filter((s) => !dismissed.includes(key(s)));
  if (!open.length && !merged.length) return null;
  return (
    <div className="space-y-3">
      {open.map((s) => (
        <Card key={key(s)} accent="var(--color-info)">
          <p className="text-sm"><b>{s.other.name}</b> and <b>{s.keep.name}</b> look like the same {kind(s.entity)}: {s.matched.toLocaleString()} records from the last {s.window_days} days match (same amount, within {s.window}). If you add both, these are counted twice. Counting once is quick for most businesses; a history of hundreds of thousands of records can take a minute.</p>
          {isOwner
            ? <div className="mt-3 flex flex-wrap gap-2">
                <button className={primaryCls} disabled={busy} onClick={() => act(async () => `Counted once: ${(await connectorApi.merge(tenantId, s.other.id, s.keep.id)).counted_once} records matched. ${s.keep.name} is the main record.`)}>Count them once</button>
                <button className={btnCls} disabled={busy} onClick={() => keepSeparate(s)}>They are different, keep separate</button>
              </div>
            : <p className="mt-2 text-xs text-ink-faint">Ask the owner to confirm.</p>}
        </Card>
      ))}
      {merged.map((m) => (
        <Card key={`${m.other.id}:${m.entity}`}>
          <p className="text-sm"><b>{m.other.name}</b> {kind(m.entity)} are counted once with <b>{m.keep.name}</b>: {m.counted_once.toLocaleString()} records matched. The lists below cover the last {m.window_days} days.</p>
          <div className="mt-2 space-y-1">
            <OnlyIn name={m.other.name} here={m.other.only_here} />
            <OnlyIn name={m.keep.name} here={m.keep.only_here} />
          </div>
          {isOwner && <button className={`${btnCls} mt-3`} disabled={busy} onClick={() => act(async () => `Undone: ${(await connectorApi.unmerge(tenantId, m.other.id)).restored} records count again.`)}>Undo</button>}
        </Card>
      ))}
    </div>
  );
}
