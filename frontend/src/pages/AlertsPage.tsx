import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Alert, type Tenant } from "../api/client";
import { Shell } from "../components/Shell";
import { FilterBar } from "../components/FilterBar";
import { AlertRow } from "../components/AlertRow";
import { Empty, PageTitle, Skeleton, btnCls } from "../components/ui";
import { useT } from "../lib/i18n";

const ALL = ["critical", "high", "medium", "informational", "opportunity"];

function Body({ tenant }: { tenant: Tenant }) {
  const t = useT();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [sev, setSev] = useState<Set<string>>(new Set(ALL));
  const [state, setState] = useState("open");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState<string | null>(null);
  const latest = useRef(0);       // only the newest request may update the page: a slow older answer must not overwrite it
  const load = useCallback(() => {
    const mine = ++latest.current;
    setLoading(true); setError(null);
    api.listAlerts(tenant.id, { state: state || undefined })
      .then((a) => { if (mine === latest.current) setAlerts(a); })
      .catch((e) => { if (mine === latest.current) setError(e.message); })
      .finally(() => { if (mine === latest.current) setLoading(false); });
  }, [tenant.id, state]);
  useEffect(() => { load(); }, [load]);
  const checkNow = () => {
    setChecking(true); setChecked(null);
    api.runChecks(tenant.id)
      .then((r) => { setChecked(r.errors ? `Checked, but ${r.errors} check(s) failed. Try again in a minute.` : `Checked just now. ${r.alerts.length ? `${r.alerts.length} alert(s) updated.` : "Nothing new to report."}`); load(); })
      .catch((e) => setChecked(`Could not run the check: ${e.message}`))
      .finally(() => setChecking(false));
  };
  const toggle = (s: string) => setSev((p) => { const n = new Set(p); if (n.has(s)) n.delete(s); else n.add(s); return n; });
  const shown = alerts.filter((a) => sev.has(a.severity));
  return (
    <>
      <PageTitle title={t("nav.alerts")} sub="Most important first. Alerts compare complete days, so today's records count from tomorrow."
        action={<div className="flex items-center gap-3"><button onClick={checkNow} disabled={checking} className={btnCls}>{checking ? "Checking…" : "Check now"}</button></div>} />
      {checked && <p role="status" className="text-sm text-ink-muted">{checked}</p>}
      {error && <p role="alert" className="text-sm text-critical">Couldn't load alerts: {error} <button onClick={load} className="text-info font-medium hover:underline">Try again</button></p>}
      <section className="space-y-3">
        <FilterBar activeSeverities={sev} onToggleSeverity={toggle} state={state} onStateChange={setState} />
        {loading && !alerts.length && <div className="space-y-2"><Skeleton className="h-20" /><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
        {!loading && shown.length === 0 && <Empty>No alerts match these filters. If you chose "Open", nothing needs your attention right now.</Empty>}
        {shown.length > 0 && <ul className="bg-panel border border-border rounded-xl divide-y divide-border overflow-hidden">{shown.map((a) => <li key={a.id}><AlertRow alert={a} tenantId={tenant.id} /></li>)}</ul>}
      </section>
    </>
  );
}

export function AlertsPage() { return <Shell wide>{(tenant) => <Body tenant={tenant} />}</Shell>; }
