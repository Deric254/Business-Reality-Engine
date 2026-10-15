import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import type { Tenant, CoverageRow } from "../api/client";
import { api, logout } from "../api/client";
import { cache } from "../api/cache";
import { COVERAGE_COLOR } from "../lib/severity";
import { useT, setLang, getLang, type Lang, type TKey } from "../lib/i18n";
import { Logo } from "./Logo";

const I = ({ children }: { children: ReactNode }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
const ICONS: Record<string, ReactNode> = {
  overview: <I><path d="M3 12 12 4l9 8" /><path d="M5 10v10h14V10" /></I>,
  alerts: <I><path d="M6 17V11a6 6 0 1 1 12 0v6l1.5 2h-15z" /><path d="M10 21h4" /></I>,
  data: <I><ellipse cx="12" cy="6" rx="8" ry="3" /><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" /><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></I>,
  business: <I><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></I>,
  accuracy: <I><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /></I>,
  settings: <I><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></I>,
};

const NAV: { to: string; key: TKey; icon: string; end?: boolean }[] = [
  { to: "/", key: "nav.overview", icon: "overview", end: true },
  { to: "/alerts", key: "nav.alerts", icon: "alerts" },
  { to: "/data", key: "nav.data", icon: "data" },
  { to: "/business", key: "nav.business", icon: "business" },
  { to: "/accuracy", key: "nav.accuracy", icon: "accuracy" },
  { to: "/settings", key: "nav.settings", icon: "settings" },
];

const COVERAGE_TEXT: Record<string, string> = {
  live: "Data is up to date", recent: "Data is recent", stale: "Some data is out of date", not_connected: "No data connected yet",
};

export function Sidebar({ tenants, activeTenant, onTenantChange }: { tenants: Tenant[]; activeTenant: Tenant | null; onTenantChange: (t: Tenant) => void }) {
  const t = useT();
  const [coverage, setCoverage] = useState<CoverageRow[]>([]);

  useEffect(() => {
    if (!activeTenant) return;
    const id = activeTenant.id;
    setCoverage(cache.coverage.get(id) ?? []);
    api.getCoverage(id).then((rows) => { cache.coverage.set(id, rows); setCoverage(rows); }).catch(() => setCoverage([]));
  }, [activeTenant]);

  const rank = { live: 0, recent: 1, stale: 2, not_connected: 3 } as const;
  const worst = coverage.reduce<string>((w, r) => ((rank[r.status] ?? 0) > (rank[w as keyof typeof rank] ?? -1) ? r.status : w), "live");

  const link = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 rounded-sm px-3 py-2 text-[0.9375rem] transition-colors ${isActive ? "bg-info text-white font-semibold" : "text-ink-muted hover:bg-panel hover:text-ink-text font-medium"}`;
  const pill = ({ isActive }: { isActive: boolean }) =>
    `whitespace-nowrap rounded-full px-3.5 py-1.5 text-sm ${isActive ? "bg-info text-white font-semibold" : "bg-panel text-ink-muted font-medium border border-border"}`;

  const business = tenants.length > 1 ? (
    <select aria-label="Business" value={activeTenant?.id || ""} className="w-full text-sm bg-transparent text-ink-text font-medium -ml-1"
      onChange={(e) => { const x = tenants.find((y) => y.id === e.target.value); if (x) onTenantChange(x); }}>
      {tenants.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
    </select>
  ) : <div className="text-sm font-medium text-ink-text truncate">{activeTenant?.name ?? tenants[0]?.name}</div>;

  return (
    <>
      {/* Desktop rail */}
      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-64 flex-col gap-6 border-r border-border bg-ink px-4 py-6">
        <Link to="/" aria-label="DericBI home" className="px-1 pt-1"><Logo size={60} /></Link>
        <div className="rounded-lg border border-border bg-panel px-3 py-2.5">
          <div className="text-xs text-ink-faint mb-0.5">Business</div>
          {business}
        </div>
        <nav aria-label="Main" className="flex flex-col gap-1">
          {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.end} className={link}>{ICONS[n.icon]}{t(n.key)}</NavLink>)}
        </nav>
        <div className="mt-auto space-y-3 px-2">
          {coverage.length > 0 && (
            <div className="flex items-center gap-2 text-xs text-ink-muted">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: COVERAGE_COLOR[worst] }} aria-hidden />{COVERAGE_TEXT[worst]}
            </div>
          )}
          <div className="flex items-center justify-between gap-2">
            <select aria-label={t("lang.label")} value={getLang()} onChange={(e) => setLang(e.target.value as Lang)} className="text-sm bg-panel border border-field rounded-sm px-2 py-1 text-ink-text">
              <option value="en">English</option><option value="sw">Kiswahili</option>
            </select>
            <button onClick={logout} className="text-sm font-medium text-ink-muted hover:text-ink-text">{t("nav.signout")}</button>
          </div>
        </div>
      </aside>

      {/* Mobile / tablet top bar */}
      <header className="lg:hidden bg-ink border-b border-border sticky top-0 z-20">
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <Link to="/" aria-label="DericBI home"><Logo size={36} /></Link>
          <div className="flex items-center gap-3">
            <select aria-label={t("lang.label")} value={getLang()} onChange={(e) => setLang(e.target.value as Lang)} className="text-sm bg-panel border border-field rounded-sm px-2 py-1">
              <option value="en">EN</option><option value="sw">SW</option>
            </select>
            <button onClick={logout} className="text-sm font-medium text-ink-muted">{t("nav.signout")}</button>
          </div>
        </div>
        <nav aria-label="Main" className="flex gap-2 overflow-x-auto px-4 pb-3">
          {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.end} className={pill}>{t(n.key)}</NavLink>)}
        </nav>
      </header>
    </>
  );
}
