import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import type { Tenant, Alert, KpiBreakdown } from "../api/client";
import { api } from "../api/client";
import { Shell } from "../components/Shell";
import { AskBox } from "../components/AskBox";
import { KpiStrip } from "../components/KpiStrip";
import { FilterBar } from "../components/FilterBar";
import { AlertRow } from "../components/AlertRow";
import { Skeleton } from "../components/ui";
import { SEVERITY_COLOR } from "../lib/severity";
import { useT } from "../lib/i18n";

const ALL_SEVERITIES = ["critical", "high", "medium", "informational", "opportunity"];

function DashboardBody({ tenant }: { tenant: Tenant }) {
  const t = useT();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [kpis, setKpis] = useState<KpiBreakdown | null>(null);
  const [activeSeverities, setActiveSeverities] = useState<Set<string>>(new Set(ALL_SEVERITIES));
  const [state, setState] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([api.listAlerts(tenant.id, { state: state || undefined }), api.getKpis(tenant.id, 30)])
      .then(([a, k]) => { setAlerts(a); setKpis(k); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [tenant.id, state]);

  useEffect(() => { refresh(); }, [refresh]);

  const toggleSeverity = (sev: string) =>
    setActiveSeverities((prev) => {
      const next = new Set(prev);
      if (next.has(sev)) next.delete(sev); else next.add(sev);
      return next;
    });

  const visibleAlerts = alerts.filter((a) => activeSeverities.has(a.severity));
  const criticalCount = alerts.filter((a) => a.severity === "critical").length;
  const highCount = alerts.filter((a) => a.severity === "high").length;
  const largestContributor = kpis?.contributors[0];

  // One plain sentence that answers "do I need to do anything today?"
  const headline = criticalCount > 0
    ? t(criticalCount === 1 ? "dash.critical.one" : "dash.critical.many", { n: criticalCount })
    : highCount > 0
      ? t(highCount === 1 ? "dash.high.one" : "dash.high.many", { n: highCount })
      : t("dash.allClear");
  const headlineColor = criticalCount > 0 ? SEVERITY_COLOR.critical : highCount > 0 ? SEVERITY_COLOR.high : SEVERITY_COLOR.opportunity;
  const noData = !loading && alerts.length === 0 && kpis && kpis.revenue.current === 0 && kpis.revenue.previous === 0;

  return (
    <>
      <div>
        {loading && !kpis ? (
          <Skeleton className="h-9 w-3/4 max-w-lg" />
        ) : (
          <h1 className="flex items-center gap-3 text-2xl font-bold tracking-tight">
            <span className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: headlineColor }} aria-hidden />
            {headline}
          </h1>
        )}
        <p className="text-sm text-ink-muted mt-1.5">{t("dash.sub")}</p>
      </div>

      {error && (
        <div role="alert" className="bg-panel border border-border border-l-4 border-l-critical rounded-sm px-4 py-3 text-sm">
          Couldn't load your data: {error}. <button onClick={refresh} className="text-info font-medium hover:underline">Try again</button>
        </div>
      )}

      {noData && (
        <div className="bg-panel border border-border border-l-4 border-l-info rounded-sm px-4 py-3 text-sm">
          No data yet. <Link to="/setup" className="text-info font-medium hover:underline">Run the 3-step setup</Link> to connect your numbers.
        </div>
      )}

      {loading && !kpis ? (
        <Skeleton className="h-28 w-full" />
      ) : (
        kpis && (
          <KpiStrip
            revenue={kpis.revenue}
            profit={kpis.profit}
            criticalCount={criticalCount}
            highCount={highCount}
            largestContributor={largestContributor?.name || null}
            largestContributorPct={largestContributor?.delta_pct ?? null}
          />
        )
      )}

      <AskBox key={tenant.id} tenantId={tenant.id} />

      <section aria-label="Alerts" className="space-y-3">
        <FilterBar activeSeverities={activeSeverities} onToggleSeverity={toggleSeverity} state={state} onStateChange={setState} />
        {loading && alerts.length === 0 && (
          <div className="space-y-2" aria-busy="true"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>
        )}
        {!loading && visibleAlerts.length === 0 && (
          <div className="text-ink-muted text-sm py-10 text-center border border-dashed border-field rounded-sm bg-panel">
            No alerts match these filters. Nothing needs attention right now.
          </div>
        )}
        {visibleAlerts.length > 0 && (
          <ul className="bg-panel border border-border rounded-sm divide-y divide-border overflow-hidden">
            {visibleAlerts.map((alert) => (
              <li key={alert.id}><AlertRow alert={alert} tenantId={tenant.id} /></li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

export function Dashboard() {
  return <Shell wide>{(tenant) => <DashboardBody tenant={tenant} />}</Shell>;
}
