import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Tenant, CoverageRow } from "../api/client";
import { api, logout } from "../api/client";
import { COVERAGE_COLOR } from "../lib/severity";

interface TopBarProps {
  tenants: Tenant[];
  activeTenant: Tenant | null;
  onTenantChange: (tenant: Tenant) => void;
}

export function TopBar({ tenants, activeTenant, onTenantChange }: TopBarProps) {
  const [coverage, setCoverage] = useState<CoverageRow[]>([]);

  useEffect(() => {
    if (!activeTenant) return;
    api.getCoverage(activeTenant.id).then(setCoverage).catch(() => setCoverage([]));
  }, [activeTenant]);

  const worstStatus = coverage.reduce<string>((worst, row) => {
    const rank = { live: 0, recent: 1, stale: 2, not_connected: 3 };
    return (rank[row.status] ?? 0) > (rank[worst as keyof typeof rank] ?? -1) ? row.status : worst;
  }, "live");

  return (
    <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 sm:px-6 py-4 border-b border-border">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-sm font-semibold tracking-tight text-ink-text">Business Reality</span>
        {tenants.length > 0 && (
          <select
            value={activeTenant?.id || ""}
            onChange={(e) => {
              const t = tenants.find((x) => x.id === e.target.value);
              if (t) onTenantChange(t);
            }}
            className="text-sm bg-panel border border-border rounded-sm px-2 py-1 text-ink-text"
          >
            {tenants.map((t) => (
              <option key={t.id} value={t.id} className="bg-panel">
                {t.name}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {coverage.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-ink-muted">
            <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: COVERAGE_COLOR[worstStatus] }} aria-hidden />
            data coverage: {worstStatus.replace("_", " ")}
          </div>
        )}
        <Link to="/settings" className="text-xs text-ink-muted hover:text-ink-text">Settings</Link>
        <button onClick={logout} className="text-xs text-ink-muted hover:text-ink-text">Sign out</button>
      </div>
    </header>
  );
}
