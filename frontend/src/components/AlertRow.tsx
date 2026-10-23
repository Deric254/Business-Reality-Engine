import { Link } from "react-router-dom";
import type { Alert } from "../api/client";
import { SEVERITY_COLOR, SEVERITY_LABEL, SEVERITY_TEXT, SEVERITY_TINT } from "../lib/severity";

export function AlertRow({ alert, tenantId }: { alert: Alert; tenantId: string }) {
  const sev = alert.severity;
  return (
    <Link to={`/alert/${alert.id}?tenant=${tenantId}`}
      className="flex gap-4 px-4 sm:px-5 py-4 hover:bg-panel-raised transition-colors group relative" style={{ boxShadow: `inset 4px 0 0 ${SEVERITY_COLOR[sev]}` }}>
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold rounded-full px-2.5 py-0.5" style={{ color: SEVERITY_TEXT[sev], backgroundColor: SEVERITY_TINT[sev] }}>
            {SEVERITY_LABEL[sev] ?? sev}
          </span>
          {alert.escalated && <span className="text-xs font-semibold text-high" title="Nobody has responded to this alert yet, and it has been waiting longer than it should">Not yet reviewed</span>}
          {!!alert.correlated_alert_ids?.length && <span className="text-xs text-ink-muted" title="Other open alerts about the same records. Open this alert to see them.">+{alert.correlated_alert_ids.length} related alert{alert.correlated_alert_ids.length === 1 ? "" : "s"}</span>}
        </div>
        <div className="text-base font-semibold text-ink-text mt-1.5 leading-snug">{alert.title}</div>
        <p className="text-sm text-ink-muted mt-0.5 line-clamp-3">{alert.what_happened}</p>
        {alert.period_label && <p className="text-xs text-ink-faint mt-0.5 kpi-figure">Days covered: {alert.period_label}, complete days only</p>}
        {alert.recommended_action && <p className="text-sm text-ink-text mt-1.5"><span className="font-medium">Next step:</span> {alert.recommended_action}</p>}
      </div>
      <span className="hidden sm:block text-ink-faint text-xl leading-none self-center group-hover:text-info transition-colors" aria-hidden>›</span>
    </Link>
  );
}
