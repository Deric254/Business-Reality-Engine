import { Link } from "react-router-dom";
import type { Alert } from "../api/client";
import { SEVERITY_COLOR, SEVERITY_LABEL, SEVERITY_TEXT, SEVERITY_TINT } from "../lib/severity";

export function AlertRow({ alert, tenantId }: { alert: Alert; tenantId: string }) {
  const sev = alert.severity;
  return (
    <Link
      to={`/alert/${alert.id}?tenant=${tenantId}`}
      className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-4 px-4 sm:px-5 py-4 hover:bg-panel-raised transition-colors group relative"
    >
      <span
        className="inline-flex items-center gap-1.5 text-xs font-semibold rounded-full px-2.5 py-0.5 sm:mt-0.5 shrink-0 sm:w-28 justify-center self-start"
        style={{ color: SEVERITY_TEXT[sev], backgroundColor: SEVERITY_TINT[sev] }}
      >
        <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: SEVERITY_COLOR[sev] }} aria-hidden />
        {SEVERITY_LABEL[sev] ?? sev}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-base font-semibold text-ink-text">
            {alert.escalated && <span className="text-xs font-bold text-high mr-2" title="Not acted on in time">Overdue</span>}
            {alert.title}
            {!!alert.correlated_alert_ids?.length && (
              <span className="text-xs font-normal text-ink-muted ml-2" title="Other open alerts about the same records">+{alert.correlated_alert_ids.length} related</span>
            )}
          </span>
          {alert.data_coverage_pct !== null && (
            <span className="text-xs text-ink-muted kpi-figure shrink-0 hidden sm:inline" title="How much of the data behind this alert was available">
              {alert.data_coverage_pct.toFixed(0)}% of data
            </span>
          )}
        </div>
        <p className="text-sm text-ink-muted mt-0.5">{alert.what_happened}</p>
      </div>
      <span className="hidden sm:block text-ink-faint text-lg leading-none mt-0.5 group-hover:text-info transition-colors" aria-hidden>›</span>
    </Link>
  );
}
