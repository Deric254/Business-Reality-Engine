import { useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import type { AlertDetail } from "../api/client";
import { api } from "../api/client";
import { SEVERITY_COLOR, SEVERITY_LABEL, timeAgo } from "../lib/severity";

const ACTIONS = [
  { state: "investigating", label: "Investigate" },
  { state: "action_taken", label: "We're doing this" },
  { state: "resolved", label: "Already handled" },
  { state: "dismissed", label: "Not doing this" },
];

export function AlertDetailPage() {
  const { alertId } = useParams<{ alertId: string }>();
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant") || "";

  const [alert, setAlert] = useState<AlertDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);

  const load = () => {
    if (!alertId || !tenantId) return;
    api
      .getAlertDetail(alertId, tenantId)
      .then(setAlert)
      .catch((e) => setError(e.message));
  };

  useEffect(load, [alertId, tenantId]);

  const takeAction = async (state: string) => {
    if (!alertId) return;
    setUpdating(true);
    try {
      await api.updateAlertState(alertId, tenantId, state);
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUpdating(false);
    }
  };

  if (error) {
    return (
      <div className="min-h-screen bg-ink flex items-center justify-center">
        <div className="text-critical text-sm">{error}</div>
      </div>
    );
  }

  if (!alert) {
    return (
      <div className="min-h-screen bg-ink flex items-center justify-center">
        <div className="text-ink-faint text-sm">Loading…</div>
      </div>
    );
  }

  const color = SEVERITY_COLOR[alert.severity];

  return (
    <div className="min-h-screen bg-ink">
      <header className="px-6 py-4 border-b border-border">
        <Link to="/" className="text-xs text-ink-muted hover:text-ink-text">
          ← Back to alerts
        </Link>
      </header>

      <main className="max-w-2xl mx-auto px-6 py-8">
        <div className="flex items-center gap-2 mb-2">
          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} aria-hidden />
          <span className="text-xs" style={{ color }}>
            {SEVERITY_LABEL[alert.severity]}
          </span>
          <span className="text-xs text-ink-faint">· {timeAgo(alert.created_at)}</span>
          {alert.escalated && <span className="text-xs font-semibold text-[#f0883e]">· ESCALATED: not acted on in time</span>}
        </div>
        {!!alert.correlated_alert_ids?.length && (
          <p className="text-xs text-ink-muted mb-3">
            Related open alerts about the same records:{" "}
            {alert.correlated_alert_ids.map((id, i) => (
              <span key={id}>{i > 0 && ", "}<a className="text-info underline" href={`/alert/${id}?tenant=${new URLSearchParams(window.location.search).get("tenant") ?? ""}`}>view</a></span>
            ))}
          </p>
        )}

        <h1 className="text-xl font-semibold text-ink-text mb-4">{alert.title}</h1>

        <p className="text-sm text-ink-muted leading-relaxed mb-6">{alert.what_happened}</p>

        {alert.ai_summary && (
          <div className="border-l-2 border-info bg-panel px-4 py-3 mb-6">
            <div className="text-xs text-info mb-1">AI summary</div>
            <p className="text-sm text-ink-text leading-relaxed">{alert.ai_summary}</p>
          </div>
        )}

        {alert.possible_drivers.length > 0 && (
          <div className="mb-6">
            <div className="text-xs text-ink-muted mb-2">Possible drivers</div>
            <div className="flex flex-col gap-2">
              {alert.possible_drivers.map((d, i) => (
                <div key={i} className="bg-panel px-3 py-2 border-l-2 border-high">
                  <div className="text-sm text-ink-text">{d.driver}</div>
                  <div className="text-xs text-ink-muted mt-0.5">{d.evidence}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {alert.ruled_out.length > 0 && (
          <div className="mb-6">
            <div className="text-xs text-ink-muted mb-2">Ruled out</div>
            <div className="flex flex-col gap-2">
              {alert.ruled_out.map((d, i) => (
                <div key={i} className="bg-panel px-3 py-2 border-l-2 border-ink-faint opacity-70">
                  <div className="text-sm text-ink-text">{d.driver.replace(" (ruled out)", "")}</div>
                  <div className="text-xs text-ink-muted mt-0.5">{d.evidence}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {alert.historical_experience.length > 0 && (
          <div className="mb-6">
            <div className="text-xs text-ink-muted mb-2">Last times this happened</div>
            <div className="flex flex-col gap-2">
              {alert.historical_experience.map((h) => (
                <div key={h.case_id} className="bg-panel px-3 py-2 border-l-2 border-info">
                  <div className="text-xs text-ink-faint">
                    {new Date(h.when.endsWith("Z") ? h.when : h.when + "Z").toLocaleDateString()}
                    {h.decision ? ` · ${h.decision.replace(/_/g, " ")}` : ""}
                    {h.attribution_confidence ? ` · attribution ${h.attribution_confidence}` : ""}
                  </div>
                  {h.intervention && <div className="text-sm text-ink-text mt-0.5">{h.intervention}</div>}
                  {h.outcome && <div className="text-xs text-ink-muted mt-0.5">{h.outcome}</div>}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="flex gap-6 mb-6 text-xs">
          <div>
            <div className="text-ink-faint">Confidence</div>
            <div className="kpi-figure text-ink-text mt-0.5">
              {alert.confidence !== null ? `${(alert.confidence * 100).toFixed(0)}%` : "—"}
            </div>
          </div>
          <div>
            <div className="text-ink-faint">Data coverage</div>
            <div className="kpi-figure text-ink-text mt-0.5">
              {alert.data_coverage_pct !== null ? `${alert.data_coverage_pct.toFixed(0)}%` : "—"}
            </div>
          </div>
        </div>

        {alert.recommended_action && (
          <div className="mb-8">
            <div className="text-xs text-ink-muted mb-1">Recommended</div>
            <p className="text-sm text-ink-text">{alert.recommended_action}</p>
          </div>
        )}

        <div className="flex gap-2 flex-wrap pt-4 border-t border-border">
          {ACTIONS.map((a) => (
            <button
              key={a.state}
              onClick={() => takeAction(a.state)}
              disabled={updating || alert.state === a.state}
              className="text-xs px-3 py-1.5 border border-border rounded-sm text-ink-text hover:border-info disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {alert.state === a.state ? `✓ ${a.label}` : a.label}
            </button>
          ))}
        </div>
      </main>
    </div>
  );
}
