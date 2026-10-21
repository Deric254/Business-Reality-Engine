import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { businessApi, type CheckFinding, type DataCheckResult } from "../api/client";
import { Card, btnCls, primaryCls } from "./ui";

const COLOUR: Record<CheckFinding["level"], string> = { problem: "#d92d20", warning: "#d4a017", info: "#3b82f6", ok: "#1f9254" };
const ORDER: CheckFinding["level"][] = ["problem", "warning", "info", "ok"];

/** Says exactly why the dashboard shows what it shows: per source, what was read, counted and left out, and what to do. */
export function DataCheck({ tenantId, days, onShowDays }: { tenantId: string; days: number; onShowDays?: (d: number) => void }) {
  const [result, setResult] = useState<DataCheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);

  useEffect(() => {
    let live = true;
    businessApi.dataCheck(tenantId, days).then((r) => { if (live) { setResult(r); setError(null); } })
      .catch(() => { if (live) setError("Could not run the check. Reload the page; if it keeps failing, the app may not have finished starting."); });
    return () => { live = false; };
  }, [tenantId, days, round]);

  if (error) return <Card accent={COLOUR.problem}><p role="alert" className="text-sm text-critical">{error}</p></Card>;
  if (!result) return <Card><p className="text-sm text-ink-muted">Checking your data…</p></Card>;
  const findings = [...result.findings].sort((a, b) => ORDER.indexOf(a.level) - ORDER.indexOf(b.level));
  const verdictColour = result.verdict === "problem" ? COLOUR.problem : result.verdict === "attention" ? COLOUR.warning : COLOUR.ok;
  return (
    <Card accent={verdictColour} className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{result.headline}</h2>
          {result.range.counted > 0 && <p className="text-sm text-ink-muted mt-0.5">{result.range.counted.toLocaleString()} records counted, from {result.range.first} to {result.range.last}; {result.range.in_view.toLocaleString()} of them in the last {result.range.period_days} days.</p>}
        </div>
        <div className="flex gap-2">
          {result.range.suggest_days && onShowDays && <button className={primaryCls} onClick={() => onShowDays(result.range.suggest_days!)}>Show the last {result.range.suggest_days} days</button>}
          <button className={btnCls} onClick={() => setRound((n) => n + 1)}>Check again</button>
        </div>
      </div>

      <ul className="space-y-2">
        {findings.map((f, i) => (
          <li key={i} className="flex gap-3 text-sm">
            <span className="w-2.5 h-2.5 rounded-full shrink-0 mt-1.5" style={{ backgroundColor: COLOUR[f.level] }} aria-hidden />
            <span><b>{f.source ? `${f.source}: ` : ""}</b>{f.text}{f.action && <span className="text-ink-muted"> {f.action}</span>}
              {f.text.includes("AI assistant is not connected") && <> <Link to="/settings#ai" className="text-info hover:underline">Connect it</Link></>}</span>
          </li>
        ))}
        {findings.length === 0 && <li className="text-sm text-ink-muted">Nothing is wrong that I can find.</li>}
      </ul>

      {result.sources.length > 0 && (
        <div className="overflow-x-auto">
          <table className="text-sm text-left w-full">
            <thead className="text-ink-muted"><tr><th className="pr-4 py-1 font-medium">Source</th><th className="pr-4 font-medium">Counted</th><th className="pr-4 font-medium">Left out</th><th className="pr-4 font-medium">Records from</th><th className="font-medium">Last sync</th></tr></thead>
            <tbody>{result.sources.map((s) => (
              <tr key={s.id} className="border-t border-border">
                <td className="pr-4 py-1.5">{s.name}</td><td className="pr-4">{s.counted.toLocaleString()}</td><td className="pr-4">{s.rejected.toLocaleString()}</td>
                <td className="pr-4 whitespace-nowrap">{s.first_record ? `${s.first_record} to ${s.last_record}` : "none"}</td>
                <td>{s.status ?? "never"}</td>
              </tr>))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-ink-faint">Database {result.schema.current ? "is up to date" : `is behind this version (${result.schema.database ?? "unknown"}, expected ${result.schema.expected})`}.</p>
    </Card>
  );
}
