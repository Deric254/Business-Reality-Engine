import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import type { Tenant, CoverageRow } from "../api/client";
import { api, logout } from "../api/client";
import { COVERAGE_COLOR } from "../lib/severity";
import { useT, setLang, getLang, type Lang } from "../lib/i18n";

interface TopBarProps {
  tenants: Tenant[];
  activeTenant: Tenant | null;
  onTenantChange: (tenant: Tenant) => void;
}

const link = ({ isActive }: { isActive: boolean }) =>
  `text-xs ${isActive ? "text-ink-text border-b border-info" : "text-ink-muted hover:text-ink-text"} pb-0.5`;

export function TopBar({ tenants, activeTenant, onTenantChange }: TopBarProps) {
  const t = useT();
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
        <Link to="/" className="text-sm font-semibold tracking-tight text-ink-text">Business Reality</Link>
        {tenants.length > 1 && (
          <select
            aria-label="Business"
            value={activeTenant?.id || ""}
            onChange={(e) => {
              const x = tenants.find((y) => y.id === e.target.value);
              if (x) onTenantChange(x);
            }}
            className="text-sm bg-panel border border-border rounded-sm px-2 py-1 text-ink-text"
          >
            {tenants.map((x) => (<option key={x.id} value={x.id} className="bg-panel">{x.name}</option>))}
          </select>
        )}
        {tenants.length === 1 && <span className="text-sm text-ink-muted">{tenants[0].name}</span>}
        <nav className="flex flex-wrap items-center gap-x-4" aria-label="Main">
          <NavLink to="/" end className={link}>{t("nav.alerts")}</NavLink>
          <NavLink to="/business" className={link}>{t("nav.business")}</NavLink>
          <NavLink to="/data" className={link}>{t("nav.data")}</NavLink>
          <NavLink to="/accuracy" className={link}>{t("nav.accuracy")}</NavLink>
          <NavLink to="/settings" className={link}>{t("nav.settings")}</NavLink>
        </nav>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {coverage.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-ink-muted">
            <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: COVERAGE_COLOR[worstStatus] }} aria-hidden />
            data coverage: {worstStatus.replace("_", " ")}
          </div>
        )}
        <select
          aria-label={t("lang.label")}
          value={getLang()}
          onChange={(e) => setLang(e.target.value as Lang)}
          className="text-xs bg-panel border border-border rounded-sm px-1.5 py-1 text-ink-muted"
        >
          <option value="en">English</option>
          <option value="sw">Kiswahili</option>
        </select>
        <button onClick={logout} className="text-xs text-ink-muted hover:text-ink-text">{t("nav.signout")}</button>
      </div>
    </header>
  );
}
