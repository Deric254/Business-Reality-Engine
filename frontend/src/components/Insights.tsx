import { useEffect, useState } from "react";
import { businessApi, type Insight } from "../api/client";
import { Card } from "./ui";

const COLOUR = { high: "#d92d20", medium: "#d4a017", good: "#12b76a" } as const;
const HEAD = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const SHOW = (v: string | number | boolean | null) => (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : typeof v === "boolean" ? (v ? "yes" : "no") : String(v ?? ""));

/** What the business's own records say about where money is stuck or leaking, and what is going well: what it is, why, since when, what it is worth, and what to do.
 *  Shown only when there is something to say; every sentence comes from the records, with its numbers. */
export function Insights({ tenantId, currency }: { tenantId: string; currency: string }) {
  const [items, setItems] = useState<Insight[]>([]);
  useEffect(() => {
    let live = true;
    businessApi.insights(tenantId).then((r) => { if (live) setItems([...r.findings, ...(r.strengths ?? [])]); }).catch(() => { if (live) setItems([]); });
    return () => { live = false; };
  }, [tenantId]);
  if (items.length === 0) return null;
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
        </Card>
      ))}
    </section>
  );
}
