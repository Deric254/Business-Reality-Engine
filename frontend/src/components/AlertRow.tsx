import { Link } from "react-router-dom";
import type { Alert } from "../api/client";
import { SEVERITY_COLOR } from "../lib/severity";

const SEVERITY_ICON: Record<string, string> = {
  critical: "●",
  high: "●",
  medium: "●",
  informational: "●",
  opportunity: "●",
};

export function AlertRow({ alert, tenantId }: { alert: Alert; tenantId: string }) {
  const color = SEVERITY_COLOR[alert.severity] || "#8b93a1";

  return (
    <Link
      to={`/alert/${alert.id}?tenant=${tenantId}`}
      className="flex items-start gap-3 px-4 py-3 border-l-2 bg-panel hover:bg-panel-raised transition-colors group"
      style={{ borderLeftColor: color }}
    >
      <span className="text-xs mt-1" style={{ color }} aria-hidden>
        {SEVERITY_ICON[alert.severity]}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-medium text-ink-text truncate">
            {alert.escalated && <span className="text-xs font-semibold text-[#f0883e] mr-2" title="Not acted on in time">ESCALATED</span>}
            {alert.title}
            {!!alert.correlated_alert_ids?.length && (
              <span className="text-xs text-ink-faint ml-2" title="Other open alerts about the same records">+{alert.correlated_alert_ids.length} related</span>
            )}
          </span>
          {alert.data_coverage_pct !== null && (
            <span className="text-xs text-ink-faint kpi-figure shrink-0">
              {alert.data_coverage_pct.toFixed(0)}% coverage
            </span>
          )}
        </div>
        <p className="text-sm text-ink-muted mt-0.5 truncate">{alert.what_happened}</p>
      </div>
      <span className="text-ink-faint text-sm opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden>
        ▸
      </span>
    </Link>
  );
}
