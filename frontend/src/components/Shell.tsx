import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError, type Tenant } from "../api/client";
import { TopBar } from "./TopBar";

/** Page frame: loads the caller's business(es), shows the top bar, and renders the page once a business is known. */
export function Shell({ children, wide = false }: { children: (tenant: Tenant) => ReactNode; wide?: boolean }) {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [active, setActive] = useState<Tenant | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listTenants()
      .then((ts) => { setTenants(ts); setActive(ts[0] ?? null); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Can't reach the server."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-ink">
      <TopBar tenants={tenants} activeTenant={active} onTenantChange={setActive} />
      <main className={`${wide ? "max-w-6xl" : "max-w-4xl"} mx-auto px-4 sm:px-6 py-6 space-y-8`}>
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        {loading && <p className="text-sm text-ink-faint">Loading…</p>}
        {!loading && !active && !error && <p className="text-sm text-ink-muted text-center py-12">No business is linked to this account.</p>}
        {active && <div key={active.id} className="space-y-8">{children(active)}</div>}
      </main>
    </div>
  );
}
