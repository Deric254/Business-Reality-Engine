import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError, type Tenant } from "../api/client";
import { cache } from "../api/cache";
import { Sidebar } from "./Sidebar";
import { RecoveryNudge } from "./RecoveryNudge";
import { Skeleton, SlowHint, useSlow } from "./ui";

/** Page frame: loads the caller's business(es), shows the navigation, and renders the page once a business is known. */
export function Shell({ children, wide = false }: { children: (tenant: Tenant) => ReactNode; wide?: boolean }) {
  const [tenants, setTenants] = useState<Tenant[]>(cache.tenants ?? []);
  const [active, setActive] = useState<Tenant | null>(cache.tenants?.[0] ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(cache.tenants === null);
  const slow = useSlow(loading);

  useEffect(() => {
    api.listTenants()
      .then((ts) => {
        cache.tenants = ts;
        setTenants(ts);
        setActive((cur) => ts.find((t) => t.id === cur?.id) ?? ts[0] ?? null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Can't reach the server. Check your connection and reload."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen">
      <Sidebar tenants={tenants} activeTenant={active} onTenantChange={setActive} />
      <main className="lg:pl-64">
        <div className={`${wide ? "max-w-6xl" : "max-w-4xl"} mx-auto px-4 sm:px-8 py-8 lg:py-10 space-y-9`}>
          {error && <p role="alert" className="text-sm text-critical">{error}</p>}
          <RecoveryNudge />
          {loading && !error && (
            <div className="space-y-4" aria-busy="true">
              <Skeleton className="h-10 w-2/3" />
              <Skeleton className="h-40 w-full" />
              <Skeleton className="h-20 w-full" />
              {slow && <SlowHint />}
            </div>
          )}
          {!loading && !active && !error && <p className="text-sm text-ink-muted text-center py-12">No business is linked to this account.</p>}
          {active && <div key={active.id} className="space-y-9">{children(active)}</div>}
        </div>
      </main>
    </div>
  );
}
