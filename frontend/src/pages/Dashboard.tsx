import { useEffect, useState, useCallback } from "react";
import type { Tenant, Alert, KpiBreakdown } from "../api/client";
import { api } from "../api/client";
import { TopBar } from "../components/TopBar";
import { AskBox } from "../components/AskBox";
import { KpiStrip } from "../components/KpiStrip";
import { FilterBar } from "../components/FilterBar";
import { AlertRow } from "../components/AlertRow";

export function Dashboard() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [activeTenant, setActiveTenant] = useState<Tenant | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [kpis, setKpis] = useState<KpiBreakdown | null>(null);
  const [activeSeverities, setActiveSeverities] = useState<Set<string>>(
    new Set(["critical", "high", "medium", "informational", "opportunity"])
  );
  const [state, setState] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listTenants()
      .then((ts) => {
        setTenants(ts);
        if (ts.length > 0) setActiveTenant(ts[0]);
        else setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  const refresh = useCallback(() => {
    if (!activeTenant) return;
    setLoading(true);
    setError(null);
    Promise.all([
      api.listAlerts(activeTenant.id, { state: state || undefined }),
      api.getKpis(activeTenant.id, 30),
    ])
      .then(([a, k]) => {
        setAlerts(a);
        setKpis(k);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [activeTenant, state]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const toggleSeverity = (sev: string) => {
    setActiveSeverities((prev) => {
      const next = new Set(prev);
      if (next.has(sev)) next.delete(sev);
      else next.add(sev);
      return next;
    });
  };

  const visibleAlerts = alerts.filter((a) => activeSeverities.has(a.severity));
  const criticalCount = alerts.filter((a) => a.severity === "critical").length;
  const highCount = alerts.filter((a) => a.severity === "high").length;

  const largestContributor = kpis?.contributors[0];

  return (
    <div className="min-h-screen bg-ink">
      <TopBar tenants={tenants} activeTenant={activeTenant} onTenantChange={setActiveTenant} />

      <main className="max-w-5xl mx-auto px-6 py-6">
        {!activeTenant && !loading && (
          <div className="text-ink-muted text-sm py-12 text-center">
            No businesses connected yet.
          </div>
        )}

        {error && (
          <div className="border-l-2 border-critical bg-panel px-4 py-3 mb-4 text-sm text-ink-text">
            Couldn't load data: {error}
          </div>
        )}

        {activeTenant && <AskBox key={activeTenant.id} tenantId={activeTenant.id} />}

        {activeTenant && kpis && (
          <KpiStrip
            revenue={kpis.revenue}
            profit={kpis.profit}
            criticalCount={criticalCount}
            highCount={highCount}
            largestContributor={largestContributor?.name || null}
            largestContributorPct={largestContributor?.delta_pct ?? null}
          />
        )}

        {activeTenant && (
          <>
            <FilterBar
              activeSeverities={activeSeverities}
              onToggleSeverity={toggleSeverity}
              state={state}
              onStateChange={setState}
            />

            {loading && <div className="text-ink-faint text-sm py-8 text-center">Loading…</div>}

            {!loading && visibleAlerts.length === 0 && (
              <div className="text-ink-muted text-sm py-12 text-center border border-dashed border-border">
                No alerts match these filters — nothing needs attention right now.
              </div>
            )}

            <div className="flex flex-col gap-px">
              {visibleAlerts.map((alert) => (
                <AlertRow key={alert.id} alert={alert} tenantId={activeTenant.id} />
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
