import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import type { Tenant, CoverageRow } from "../api/client";
import { api, logout, masterApi } from "../api/client";
import { cache } from "../api/cache";
import { getTokens } from "../api/session";
import { COVERAGE_COLOR } from "../lib/severity";
import { useT, setLang, getLang, type Lang, type TKey } from "../lib/i18n";
import { Logo } from "./Logo";
import { LiveStamp } from "../lib/live";
import { useOnLiveChange } from "../lib/liveContext";
import { useRecoveryMissing } from "../lib/useRecoveryMissing";

const I = ({ children }: { children: ReactNode }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
const ICONS: Record<string, ReactNode> = {
  overview: <I><path d="M3 12 12 4l9 8" /><path d="M5 10v10h14V10" /></I>,
  analytics: <I><path d="M4 19V5" /><path d="M4 19h16" /><path d="m7 15 3.5-4 3 2.5L19 7" /></I>,
  alerts: <I><path d="M6 17V11a6 6 0 1 1 12 0v6l1.5 2h-15z" /><path d="M10 21h4" /></I>,
  data: <I><ellipse cx="12" cy="6" rx="8" ry="3" /><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" /><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></I>,
  business: <I><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></I>,
  accuracy: <I><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /></I>,
  goals: <I><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></I>,
  more: <I><circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" /></I>,
  settings: <I><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></I>,
};

type NavItem = { to: string; key: TKey; icon: string; end?: boolean; tab?: boolean };
/** `tab` marks the pages that get a place on the phone's tab bar; the others sit under "More". */
const NAV: NavItem[] = [
  { to: "/", key: "nav.overview", icon: "overview", end: true, tab: true },
  { to: "/analytics", key: "nav.analytics", icon: "analytics", tab: true },
  { to: "/alerts", key: "nav.alerts", icon: "alerts", tab: true },
  { to: "/goals", key: "nav.goals", icon: "goals" },
  { to: "/data", key: "nav.data", icon: "data", tab: true },
  { to: "/business", key: "nav.business", icon: "business" },
  { to: "/accuracy", key: "nav.accuracy", icon: "accuracy" },
  { to: "/settings", key: "nav.settings", icon: "settings" },
];

const COVERAGE_TEXT: Record<string, string> = {
  live: "Data is up to date", recent: "Data is recent", stale: "Some data is out of date", not_connected: "No data connected yet",
};

// Whether the signed-in account is the master, remembered per sign-in (never across different people on the same page),
// so another person signing in after the master never inherits the Licences link.
let masterKnown: { who: string; value: boolean } | null = null;
const signInId = () => getTokens()?.refresh_token ?? "";

const row = (isActive: boolean, pad: string) =>
  `flex items-center gap-3 rounded-sm px-3 ${pad} text-[0.9375rem] transition-colors ${isActive ? "bg-info text-white font-semibold" : "text-ink-muted hover:bg-panel hover:text-ink-text font-medium"}`;
const railLink = ({ isActive }: { isActive: boolean }) => row(isActive, "py-2");
const sheetLink = ({ isActive }: { isActive: boolean }) => row(isActive, "py-3");
const tabCls = (on: boolean) =>
  `flex flex-col items-center justify-start gap-1 min-h-14 w-full border-t-2 px-0.5 pt-2 pb-1.5 text-center leading-tight text-xs ${on ? "border-info text-info font-semibold" : "border-transparent text-ink-muted font-medium"}`;
const isHere = (n: NavItem, pathname: string) => (n.end ? pathname === n.to : pathname === n.to || pathname.startsWith(n.to + "/"));

/** A small dot for "this needs you", and the same message in words for people who can't see it (read after the label). */
const Dot = ({ className = "" }: { className?: string }) => <span aria-hidden="true" className={`w-2 h-2 rounded-full bg-high shrink-0 ${className}`} />;
function NeedsActionText() {
  const t = useT();
  return <span className="sr-only">, {t("nav.needsAction")}</span>;
}

/** Phone and tablet navigation: the main pages as tabs under the thumb, everything else (and the business switch and data
 *  status, which the side rail shows on a computer) under "More". Mounted with the page's path as its key, so going to
 *  another page always closes the menu. */
function PhoneNav({ tabs, more, pathname, needsAction, business, status }: {
  tabs: NavItem[]; more: NavItem[]; pathname: string; needsAction: boolean; business: ReactNode; status: ReactNode;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const moreButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); moreButton.current?.focus(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);
  const settingsHere = (n: NavItem) => n.to === "/settings" && needsAction;
  return (
    <>
      {open && <div aria-hidden="true" onClick={() => setOpen(false)} className="lg:hidden print:!hidden fixed inset-0 z-20 bg-scrim" />}
      {open && (
        <div id="more-menu" className="lg:hidden print:!hidden fixed inset-x-3 bottom-16 z-30 max-h-[70vh] overflow-y-auto rounded-xl border border-border bg-panel p-2 shadow-lg">
          <div className="px-3 py-2 mb-1 border-b border-border">
            <div className="text-xs text-ink-faint mb-0.5">{t("nav.business")}</div>
            {business}
          </div>
          <ul>
            {more.map((n) => (
              <li key={n.to}>
                <NavLink to={n.to} end={n.end} onClick={() => setOpen(false)} className={sheetLink}>
                  {ICONS[n.icon]}{t(n.key)}{settingsHere(n) && <><Dot className="ml-auto" /><NeedsActionText /></>}
                </NavLink>
              </li>
            ))}
          </ul>
          {status && <div className="px-3 pt-3 mt-1 border-t border-border">{status}</div>}
        </div>
      )}
      <nav aria-label="Main" className="tabbar lg:hidden print:!hidden fixed inset-x-0 bottom-0 z-30 bg-ink border-t border-border">
        <ul className="grid grid-flow-col auto-cols-[minmax(0,1fr)]">
          {tabs.map((n) => (
            <li key={n.to}>
              <NavLink to={n.to} end={n.end} className={({ isActive }) => tabCls(isActive)}>{ICONS[n.icon]}{t(n.key)}</NavLink>
            </li>
          ))}
          <li>
            <button ref={moreButton} type="button" aria-expanded={open} aria-controls="more-menu" onClick={() => setOpen((o) => !o)} className={tabCls(open || more.some((n) => isHere(n, pathname)))}>
              <span className="relative">{ICONS.more}{needsAction && <Dot className="absolute -top-0.5 -right-1.5" />}</span>{t("nav.more")}{needsAction && <NeedsActionText />}
            </button>
          </li>
        </ul>
      </nav>
    </>
  );
}

export function Sidebar({ tenants, activeTenant, onTenantChange }: { tenants: Tenant[]; activeTenant: Tenant | null; onTenantChange: (t: Tenant) => void }) {
  const t = useT();
  const [coverage, setCoverage] = useState<CoverageRow[]>([]);
  const [isMaster, setIsMaster] = useState(masterKnown !== null && masterKnown.who === signInId() && masterKnown.value);
  useEffect(() => {
    const who = signInId();
    if (masterKnown && masterKnown.who === who) { setIsMaster(masterKnown.value); return; }
    setIsMaster(false);
    masterApi.whoami().then((m) => { if (signInId() === who) { masterKnown = { who, value: m }; setIsMaster(m); } });
  }, []);
  const nav: NavItem[] = isMaster ? [...NAV, { to: "/master", key: "nav.licences", icon: "accuracy" }] : NAV;
  const { pathname } = useLocation();
  const recoveryMissing = useRecoveryMissing();

  useEffect(() => {
    if (!activeTenant) return;
    const id = activeTenant.id;
    setCoverage(cache.coverage.get(id) ?? []);
    api.getCoverage(id).then((rows) => { cache.coverage.set(id, rows); setCoverage(rows); }).catch(() => setCoverage([]));
  }, [activeTenant]);

  useOnLiveChange(() => {                // data, sources or alerts changed: the "up to date" line must not trail them
    if (!activeTenant) return;
    const id = activeTenant.id;
    api.getCoverage(id).then((rows) => { cache.coverage.set(id, rows); setCoverage(rows); }).catch(() => undefined);
  });

  const rank = { live: 0, recent: 1, stale: 2, not_connected: 3 } as const;
  const worst = coverage.reduce<string>((w, r) => ((rank[r.status] ?? 0) > (rank[w as keyof typeof rank] ?? -1) ? r.status : w), "live");

  const business = tenants.length > 1 ? (
    <select aria-label="Business" value={activeTenant?.id || ""} className="w-full text-sm bg-transparent text-ink-text font-medium -ml-1"
      onChange={(e) => { const x = tenants.find((y) => y.id === e.target.value); if (x) onTenantChange(x); }}>
      {tenants.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
    </select>
  ) : <div className="text-sm font-medium text-ink-text truncate">{activeTenant?.name ?? tenants[0]?.name}</div>;

  const status = coverage.length > 0 ? (
    <div className="flex items-center gap-2 text-xs text-ink-muted">
      <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: COVERAGE_COLOR[worst] }} aria-hidden />{COVERAGE_TEXT[worst]}
      <LiveStamp className="text-ink-faint" />
    </div>
  ) : null;

  return (
    <>
      {/* Desktop rail */}
      <aside className="hidden lg:flex print:!hidden fixed inset-y-0 left-0 w-64 flex-col gap-4 border-r border-border bg-ink px-4 py-5 overflow-hidden">
        <Link to="/" aria-label="DericBI home" className="px-1 pt-1 shrink-0"><Logo size={60} /></Link>
        <div className="rounded-lg border border-border bg-panel px-3 py-2.5 shrink-0">
          <div className="text-xs text-ink-faint mb-0.5">Business</div>
          {business}
        </div>
        {/* The menu scrolls on short screens; the footer (language, Sign out) is always pinned and visible. */}
        <nav aria-label="Main" className="flex flex-col gap-1 flex-1 min-h-0 overflow-y-auto">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={railLink}>
              {ICONS[n.icon]}{t(n.key)}{n.to === "/settings" && recoveryMissing && <><Dot className="ml-auto" /><NeedsActionText /></>}
            </NavLink>
          ))}
        </nav>
        <div className="shrink-0 space-y-3 px-2 pt-3 border-t border-border">
          {status}
          <div className="flex items-center justify-between gap-2">
            <select aria-label={t("lang.label")} value={getLang()} onChange={(e) => setLang(e.target.value as Lang)} className="text-sm bg-panel border border-field rounded-sm px-2 py-1 text-ink-text">
              <option value="en">English</option><option value="sw">Kiswahili</option>
            </select>
            <button onClick={logout} className="text-sm font-medium text-ink-muted hover:text-ink-text">{t("nav.signout")}</button>
          </div>
        </div>
      </aside>

      {/* Phone / tablet: a slim top bar, and the tab bar under the thumb */}
      <header className="lg:hidden print:!hidden bg-ink border-b border-border sticky top-0 z-20">
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <Link to="/" aria-label="DericBI home"><Logo size={36} /></Link>
          <div className="flex items-center gap-3">
            <select aria-label={t("lang.label")} value={getLang()} onChange={(e) => setLang(e.target.value as Lang)} className="text-sm bg-panel border border-field rounded-sm px-2 py-1">
              <option value="en">EN</option><option value="sw">SW</option>
            </select>
            <button onClick={logout} className="text-sm font-medium text-ink-muted px-1 py-2">{t("nav.signout")}</button>
          </div>
        </div>
      </header>
      <PhoneNav key={pathname} tabs={nav.filter((n) => n.tab)} more={nav.filter((n) => !n.tab)} pathname={pathname} needsAction={recoveryMissing} business={business} status={status} />
    </>
  );
}
