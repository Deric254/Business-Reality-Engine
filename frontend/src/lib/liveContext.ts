import { createContext, useContext, useEffect, useRef } from "react";

export interface LiveState { tick: number; offline: boolean; syncedAt: string | null; checkedAt: number | null }
export const LiveCtx = createContext<LiveState>({ tick: 0, offline: false, syncedAt: null, checkedAt: null });

export const useLive = () => useContext(LiveCtx);

/** Runs `reload` each time the business's data, alerts, targets or sources change. Declare it BEFORE the page's own first-load effect. */
export function useOnLiveChange(reload: () => void) {
  const { tick } = useLive();
  const latest = useRef(reload);
  const seen = useRef(tick);
  useEffect(() => { latest.current = reload; });
  useEffect(() => { if (tick !== seen.current) { seen.current = tick; latest.current(); } }, [tick]);
}
