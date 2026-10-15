import { useCallback, useEffect, useState } from "react";
import { api, type Alert, type Tenant } from "../api/client";
import { Shell } from "../components/Shell";
import { FilterBar } from "../components/FilterBar";
import { AlertRow } from "../components/AlertRow";
import { Empty, PageTitle, Skeleton } from "../components/ui";
import { useT } from "../lib/i18n";

const ALL = ["critical", "high", "medium", "informational", "opportunity"];

function Body({ tenant }: { tenant: Tenant }) {
  const t = useT();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [sev, setSev] = useState<Set<string>>(new Set(ALL));
  const [state, setState] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setLoading(true); setError(null);
    api.listAlerts(tenant.id, { state: state || undefined }).then(setAlerts).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [tenant.id, state]);
  useEffect(() => { load(); }, [load]);
  const toggle = (s: string) => setSev((p) => { const n = new Set(p); if (n.has(s)) n.delete(s); else n.add(s); return n; });
  const shown = alerts.filter((a) => sev.has(a.severity));
  return (
    <>
      <PageTitle title={t("nav.alerts")} sub="Ordered by what matters most. Open one to see the evidence, the likely causes and what to do." />
      {error && <p role="alert" className="text-sm text-critical">Couldn't load alerts: {error} <button onClick={load} className="text-info font-medium hover:underline">Try again</button></p>}
      <section className="space-y-3">
        <FilterBar activeSeverities={sev} onToggleSeverity={toggle} state={state} onStateChange={setState} />
        {loading && !alerts.length && <div className="space-y-2"><Skeleton className="h-20" /><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
        {!loading && shown.length === 0 && <Empty>No alerts match these filters. Nothing needs attention right now.</Empty>}
        {shown.length > 0 && <ul className="bg-panel border border-border rounded-xl divide-y divide-border overflow-hidden">{shown.map((a) => <li key={a.id}><AlertRow alert={a} tenantId={tenant.id} /></li>)}</ul>}
      </section>
    </>
  );
}

export function AlertsPage() { return <Shell wide>{(tenant) => <Body tenant={tenant} />}</Shell>; }
