import { useEffect, useState } from "react";
import { businessApi, type Insight, type InsightEpisode, type InsightReference } from "../api/client";
import { Card } from "./ui";

const COLOUR = { high: "#d92d20", medium: "#d4a017", good: "#12b76a" } as const;
const HEAD = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const SHOW = (v: string | number | boolean | null) => (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : typeof v === "boolean" ? (v ? "yes" : "no") : String(v ?? ""));

/** What the business's own records say about where money is stuck or leaking, and what is going well: what it is, why, since when, what it is worth, and what to do.
 *  Shown only when there is something to say; every sentence comes from the records, with its numbers. */
export function Insights({ tenantId, currency }: { tenantId: string; currency: string }) {
  const [items, setItems] = useState<Insight[]>([]);
  const [past, setPast] = useState<InsightEpisode[]>([]);
  const [reading, setReading] = useState<Record<string, InsightReference[]>>({});
  useEffect(() => {
    let live = true;
    businessApi.insights(tenantId).then((r) => { if (live) setItems([...r.findings, ...(r.strengths ?? [])]); }).catch(() => { if (live) setItems([]); });
    businessApi.insightsHistory(tenantId).then((r) => { if (live) setPast(r.episodes); }).catch(() => { if (live) setPast([]); });
    businessApi.insightsReferences(tenantId).then((r) => { if (live) setReading(r.references); }).catch(() => { if (live) setReading({}); });   // slower (a web search), so it fills in after the rest
    return () => { live = false; };
  }, [tenantId]);
  if (items.length === 0 && past.length === 0) return null;
  return (
    <section aria-label="What your records show" className="space-y-3">
      <h2 className="text-lg font-semibold">What your records show</h2>
      {items.map((f) => (
        <Card key={f.kind} accent={COLOUR[f.severity]} className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-semibold">{f.title}</h3>
            {(f.cost ?? f.gain) && <span className="text-sm font-medium">{f.gain ? "+" : ""}{currency} {Math.round((f.cost ?? f.gain)!.amount).toLocaleString("en-US")} <span className="text-ink-muted font-normal">{(f.cost ?? f.gain)!.basis}</span></span>}
          </div>
          <p>{f.what}</p>
          <p className="text-sm text-ink-muted"><strong className="text-ink-text">Why: </strong>{f.why}</p>
          {f.rows.length > 0 && (
            <div className="overflow-x-auto"><table className="text-sm w-full"><thead><tr>{Object.keys(f.rows[0]).map((k) => <th key={k} className="text-left pr-4 font-medium text-ink-muted">{HEAD(k)}</th>)}</tr></thead>
              <tbody>{f.rows.map((r, i) => <tr key={i}>{Object.values(r).map((v, j) => <td key={j} className="pr-4 py-0.5">{SHOW(v)}</td>)}</tr>)}</tbody></table></div>
          )}
          <div className="text-sm"><strong>{f.severity === "good" ? "How to keep it going" : "What to do"}</strong><ol className="list-decimal pl-5 space-y-0.5">{f.fix.map((s) => <li key={s}>{s}</li>)}</ol></div>
          {(reading[f.kind] ?? []).length > 0 && (
            <details className="text-sm"><summary className="cursor-pointer font-medium">How others handled this (from the web)</summary>
              <p className="text-xs text-ink-muted mt-1">General reading on the same kind of problem. It is not about your business, and the search sent nothing of yours.</p>
              <ul className="mt-2 space-y-2">{reading[f.kind].map((x) => (
                <li key={x.url}><a href={x.url} target="_blank" rel="noopener noreferrer" className="underline">{x.title}</a> <span className="text-ink-muted">{x.source}, read {x.fetched}</span>
                  <div className="text-ink-muted">{x.snippet}</div></li>))}</ul>
            </details>
          )}
        </Card>
      ))}
      {past.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer font-medium">History of these problems ({past.length})</summary>
          <p className="text-xs text-ink-muted mt-1">From the day this was switched on for your business. What cleared is shown as it happened; the records cannot prove what caused it.</p>
          <ul className="mt-2 space-y-2">
            {past.map((e) => (
              <li key={e.id} className="border-l-2 pl-3" style={{ borderColor: e.status === "cleared" ? COLOUR.good : e.status === "open" ? COLOUR.high : COLOUR.medium }}>
                <strong>{e.title}</strong> <span className="text-ink-muted">{e.began ? `began ${e.began}, ` : ""}first seen {e.first_seen}{e.ended ? `, ended ${e.ended}` : ""}</span>
                <div>{e.summary}</div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
