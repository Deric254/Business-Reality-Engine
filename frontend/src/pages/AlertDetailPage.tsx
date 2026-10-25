import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import type { AlertDetail } from "../api/client";
import { api } from "../api/client";
import { Shell } from "../components/Shell";
import { Card, Skeleton } from "../components/ui";
import { SEVERITY_COLOR, SEVERITY_LABEL, SEVERITY_TEXT, SEVERITY_TINT, timeAgo } from "../lib/severity";
import { useOnLiveChange } from "../lib/liveContext";

const ACTIONS = [
  { state: "investigating", label: "I'm looking into it" },
  { state: "action_taken", label: "We're acting on it" },
  { state: "resolved", label: "Already handled" },
  { state: "dismissed", label: "Not relevant" },
];

function Body({ alertId, tenantId }: { alertId: string; tenantId: string }) {
  const [alert, setAlert] = useState<AlertDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);

  const load = useCallback(() => api.getAlertDetail(alertId, tenantId).then(setAlert), [alertId, tenantId]);
  useOnLiveChange(() => { load().catch(() => undefined); });       // detection may resolve or update this alert while it is open
  useEffect(() => {
    let current = true;                  // ignore an answer that arrives after the person has moved to another alert
    setAlert(null); setError(null);
    load().catch((e) => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [load]);

  const takeAction = async (state: string) => {
    setUpdating(true);
    try { await api.updateAlertState(alertId, tenantId, state); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setUpdating(false); }
  };

  if (error) return <p role="alert" className="text-sm text-critical">{error}</p>;
  if (!alert) return <div className="space-y-4"><Skeleton className="h-10 w-2/3" /><Skeleton className="h-24" /><Skeleton className="h-32" /></div>;

  const sev = alert.severity;
  return (
    <article className="space-y-7 max-w-3xl">
      <Link to="/alerts" className="text-sm font-medium text-info hover:underline">Back to all alerts</Link>

      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center text-xs font-semibold rounded-full px-2.5 py-0.5" style={{ color: SEVERITY_TEXT[sev], backgroundColor: SEVERITY_TINT[sev] }}>{SEVERITY_LABEL[sev]}</span>
          <span className="text-sm text-ink-muted">Detected {timeAgo(alert.created_at)}</span>
          {alert.escalated && <span className="text-sm font-semibold text-high">Not yet reviewed: nobody has responded to this yet</span>}
        </div>
        <h1 className="display text-3xl sm:text-4xl" style={{ textDecorationColor: SEVERITY_COLOR[sev] }}>{alert.title}</h1>
        <p className="text-lg text-ink-muted leading-relaxed">{alert.what_happened}</p>
        {alert.related.length > 0 && (
          <div className="text-sm text-ink-muted">Also open about the same records:
            <ul className="mt-1 space-y-0.5">{alert.related.map((r) => (
              <li key={r.id}><Link className="text-info underline" to={`/alert/${r.id}?tenant=${tenantId}`}>{r.title}</Link></li>
            ))}</ul>
          </div>
        )}
      </header>

      {alert.recommended_action && (
        <Card accent="#1b5a54" className="!bg-info-soft/50">
          <div className="font-semibold">What to do next</div>
          <p className="mt-1 text-ink-text">{alert.recommended_action}</p>
        </Card>
      )}

      {alert.ai_summary && (
        <Card><div className="font-semibold mb-1">In plain words</div><p className="leading-relaxed">{alert.ai_summary}</p></Card>
      )}

      {alert.possible_drivers.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xl font-semibold tracking-tight">What is likely behind it</h2>
          {alert.possible_drivers.map((d, i) => (
            <div key={i} className="bg-panel border border-border rounded-xl px-4 py-3" style={{ boxShadow: "inset 4px 0 0 #a8400a" }}>
              <div className="font-semibold">{d.driver}</div>
              <div className="text-sm text-ink-muted mt-0.5">{d.evidence}</div>
            </div>
          ))}
        </section>
      )}

      {alert.ruled_out.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xl font-semibold tracking-tight">What we checked and ruled out</h2>
          {alert.ruled_out.map((d, i) => (
            <div key={i} className="bg-panel/70 border border-border rounded-xl px-4 py-3">
              <div className="font-medium">{d.driver.replace(" (ruled out)", "")}</div>
              <div className="text-sm text-ink-muted mt-0.5">{d.evidence}</div>
            </div>
          ))}
        </section>
      )}

      {alert.historical_experience.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xl font-semibold tracking-tight">The last times this happened</h2>
          {alert.historical_experience.map((h) => (
            <div key={h.case_id} className="bg-panel border border-border rounded-xl px-4 py-3">
              <div className="text-sm text-ink-muted">
                {new Date(h.when.endsWith("Z") ? h.when : h.when + "Z").toLocaleDateString("en-KE", { day: "numeric", month: "long", year: "numeric" })}
                {h.decision ? `: you chose to ${h.decision.replace(/_/g, " ")}` : ""}
              </div>
              {h.intervention && <div className="mt-0.5">{h.intervention}</div>}
              {h.outcome && <div className="text-sm text-ink-muted mt-0.5">{h.outcome}{h.attribution_confidence ? ` (we can only say this is ${h.attribution_confidence} confidence that your action caused it)` : ""}</div>}
            </div>
          ))}
        </section>
      )}

      <section className="grid grid-cols-2 gap-4 max-w-md">
        <div><div className="text-sm text-ink-muted">How sure we are</div><div className="figure text-2xl">{alert.confidence !== null ? `${(alert.confidence * 100).toFixed(0)}%` : "n/a"}</div><div className="text-xs text-ink-muted mt-0.5">Higher when there is more history to compare with.</div></div>
        <div><div className="text-sm text-ink-muted">How up to date your data is</div><div className="figure text-2xl">{alert.data_coverage_pct !== null ? `${alert.data_coverage_pct.toFixed(0)}%` : "n/a"}</div><div className="text-xs text-ink-muted mt-0.5">Lower when a data source has gone quiet.</div></div>
      </section>

      <section className="space-y-2 border-t border-border pt-6">
        <h2 className="text-xl font-semibold tracking-tight">Where are you with this?</h2>
        <p className="text-sm text-ink-muted">We keep a record of what you did and what followed.</p>
        <div className="flex gap-2 flex-wrap pt-1">
          {ACTIONS.map((a) => {
            const on = alert.state === a.state;
            return (
              <button key={a.state} onClick={() => takeAction(a.state)} disabled={updating || on}
                className={`text-sm font-medium px-3.5 py-2 border rounded-sm transition-colors ${on ? "bg-info text-white border-info" : "bg-panel border-field hover:bg-panel-raised"} disabled:cursor-default`}>
                {on ? `Marked: ${a.label}` : a.label}
              </button>
            );
          })}
        </div>
      </section>
    </article>
  );
}

export function AlertDetailPage() {
  const { alertId } = useParams<{ alertId: string }>();
  const [sp] = useSearchParams();
  return <Shell>{(tenant) => <Body alertId={alertId ?? ""} tenantId={sp.get("tenant") || tenant.id} />}</Shell>;
}
