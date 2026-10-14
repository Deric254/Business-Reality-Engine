import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import type { Tenant, CoverageRow } from "../api/client";
import { api, logout } from "../api/client";
import { cache } from "../api/cache";
import { COVERAGE_COLOR } from "../lib/severity";
import { useT, setLang, getLang, type Lang } from "../lib/i18n";
import { Logo } from "./Logo";

interface TopBarProps {
  tenants: Tenant[];
  activeTenant: Tenant | null;
  onTenantChange: (tenant: Tenant) => void;
}

const link = ({ isActive }: { isActive: boolean }) =>
  `whitespace-nowrap text-sm rounded-sm px-2.5 sm:px-3 py-1.5 transition-colors ${
    isActive ? "bg-info/10 text-info font-semibold" : "text-ink-muted hover:bg-ink hover:text-ink-text font-medium"
  }`;

const COVERAGE_TEXT: Record<string, string> = {
  live: "Data is up to date",
  recent: "Data is recent",
  stale: "Some data is out of date",
  not_connected: "No data connected yet",
};

export function TopBar({ tenants, activeTenant, onTenantChange }: TopBarProps) {
  const t = useT();
  const [coverage, setCoverage] = useState<CoverageRow[]>([]);

  useEffect(() => {
    if (!activeTenant) return;
    const id = activeTenant.id;
    setCoverage(cache.coverage.get(id) ?? []);
    api.getCoverage(id)
      .then((rows) => { cache.coverage.set(id, rows); setCoverage(rows); })
      .catch(() => setCoverage([]));
  }, [activeTenant]);

  const worstStatus = coverage.reduce<string>((worst, row) => {
    const rank = { live: 0, recent: 1, stale: 2, not_connected: 3 };
    return (rank[row.status] ?? 0) > (rank[worst as keyof typeof rank] ?? -1) ? row.status : worst;
  }, "live");

  return (
    <header className="bg-panel border-b border-border">
      <div className="max-w-6xl mx-auto flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 sm:px-6 py-3">
        <div className="flex items-center gap-4">
          <Link to="/" aria-label="Business Reality home"><Logo /></Link>
          {tenants.length > 1 && (
            <select
              aria-label="Business"
              value={activeTenant?.id || ""}
              onChange={(e) => {
                const x = tenants.find((y) => y.id === e.target.value);
                if (x) onTenantChange(x);
              }}
              className="text-sm bg-panel border border-field rounded-sm px-2 py-1 text-ink-text"
            >
              {tenants.map((x) => (<option key={x.id} value={x.id}>{x.name}</option>))}
            </select>
          )}
          {tenants.length === 1 && <span className="hidden sm:inline text-sm text-ink-muted border-l border-border pl-4">{tenants[0].name}</span>}
        </div>
        <div className="flex items-center gap-3">
          {coverage.length > 0 && (
            <div className="hidden md:flex items-center gap-1.5 text-xs text-ink-muted">
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: COVERAGE_COLOR[worstStatus] }} aria-hidden />
              {COVERAGE_TEXT[worstStatus] ?? worstStatus.replace("_", " ")}
            </div>
          )}
          <select
            aria-label={t("lang.label")}
            value={getLang()}
            onChange={(e) => setLang(e.target.value as Lang)}
            className="text-sm bg-panel border border-field rounded-sm px-2 py-1 text-ink-text"
          >
            <option value="en">English</option>
            <option value="sw">Kiswahili</option>
          </select>
          <button onClick={logout} className="text-sm font-medium text-ink-muted hover:text-ink-text rounded-sm px-2 py-1">{t("nav.signout")}</button>
        </div>
      </div>
      <nav className="max-w-6xl mx-auto flex items-center gap-1 px-3 sm:px-5 pb-2 overflow-x-auto" aria-label="Main">
        <NavLink to="/" end className={link}>{t("nav.alerts")}</NavLink>
        <NavLink to="/business" className={link}>{t("nav.business")}</NavLink>
        <NavLink to="/data" className={link}>{t("nav.data")}</NavLink>
        <NavLink to="/accuracy" className={link}>{t("nav.accuracy")}</NavLink>
        <NavLink to="/settings" className={link}>{t("nav.settings")}</NavLink>
      </nav>
    </header>
  );
}
