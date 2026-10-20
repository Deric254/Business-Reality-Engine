import { useState } from "react";
import { ApiError, connectorApi, type RejectedRecord } from "../api/client";

/** The records a source sent that were left out of every total, each with its reason, so it can be fixed at the source. */
export function RejectedList({ tenantId, connectorId, count }: { tenantId: string; connectorId: string; count: number }) {
  const [records, setRecords] = useState<RejectedRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  function load(open: boolean) {
    if (open && records === null) connectorApi.rejected(tenantId, connectorId).then((r) => setRecords(r.records)).catch((e) => setError(e instanceof ApiError ? e.message : "Could not load them."));
  }
  return (
    <details className="text-sm mt-1" onToggle={(e) => load(e.currentTarget.open)}>
      <summary className="cursor-pointer text-critical">{count.toLocaleString()} record{count === 1 ? "" : "s"} left out of your totals. Show which and why</summary>
      {error && <p role="alert" className="text-critical mt-1">{error}</p>}
      {records && (
        <div className="mt-1 overflow-x-auto">
          <p className="text-ink-muted mb-1">Fix them in the source and sync again; a corrected record replaces the rejected one.</p>
          <table className="text-left">
            <thead className="text-ink-muted"><tr><th className="pr-4 font-medium">When</th><th className="pr-4 font-medium">Amount</th><th className="pr-4 font-medium">Branch</th><th className="font-medium">Why</th></tr></thead>
            <tbody>{records.map((r, i) => (
              <tr key={i}><td className="pr-4 whitespace-nowrap">{r.when}</td><td className="pr-4">{r.amount != null ? r.amount.toLocaleString() : ""}</td><td className="pr-4">{r.branch ?? ""}</td><td>{r.reasons.join(", ")}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </details>
  );
}
