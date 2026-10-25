import { useEffect, useState, type ReactNode } from "react";
import { liveApi } from "../api/client";
import { parseApiDate, timeAgo } from "./severity";
import { LiveCtx, useLive, type LiveState } from "./liveContext";

// Keeps every open screen in step with the data behind it. One light call (/api/live/pulse) asks "did anything change?":
// the business's records, its alerts, targets or sources. When the answer moves, every screen that listens (useOnLiveChange)
// reloads quietly: no spinner, no dimming, the old figures simply become the new ones. One poller per page, only while the
// tab is visible, and an honest "can't reach the server" notice instead of silently showing old numbers.
const POLL_MS = 8000;
const OFFLINE_AFTER_FAILURES = 2;

export function LiveProvider({ tenantId, children }: { tenantId: string | null; children: ReactNode }) {
  const [state, setState] = useState<LiveState>({ tick: 0, offline: false, syncedAt: null, checkedAt: null });
  useEffect(() => {
    if (!tenantId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let token: string | null = null;      // the first answer is the baseline: it is asked for before the page's own data, so a change after it is never missed
    let failures = 0;
    const schedule = () => { if (!stopped) timer = setTimeout(poll, POLL_MS * (0.85 + Math.random() * 0.3)); };
    const poll = async () => {
      clearTimeout(timer);
      if (document.visibilityState === "hidden") { schedule(); return; }        // nobody is looking: ask nothing
      try {
        const p = await liveApi.pulse(tenantId);
        if (stopped) return;
        failures = 0;
        const changed = token !== null && token !== p.token;
        token = p.token;
        setState((s) => ({ tick: changed ? s.tick + 1 : s.tick, offline: false, syncedAt: p.last_synced_at, checkedAt: Date.now() }));
      } catch {
        if (stopped) return;
        failures += 1;
        if (failures >= OFFLINE_AFTER_FAILURES) setState((s) => (s.offline ? s : { ...s, offline: true }));
      }
      schedule();
    };
    const wake = () => { if (document.visibilityState === "visible") poll(); };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", wake);
    poll();
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", wake); window.removeEventListener("online", wake); };
  }, [tenantId]);
  return <LiveCtx.Provider value={state}>{children}</LiveCtx.Provider>;
}

/** One quiet line: when the newest figures arrived. Re-renders itself so "2m ago" keeps counting. */
export function LiveStamp({ className = "" }: { className?: string }) {
  const { syncedAt, offline } = useLive();
  const [, setNow] = useState(0);
  useEffect(() => { const i = setInterval(() => setNow((n) => n + 1), 30000); return () => clearInterval(i); }, []);
  if (offline) return <span className={`text-critical ${className}`} role="status">Can't reach the server. Figures may be out of date.</span>;
  if (!syncedAt) return null;
  return <span className={className} title={parseApiDate(syncedAt).toLocaleString()}>Updated {timeAgo(syncedAt)}</span>;
}
