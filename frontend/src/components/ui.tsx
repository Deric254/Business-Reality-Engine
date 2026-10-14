import { useEffect, useState, type ReactNode } from "react";

export const inputCls = "bg-panel border border-field rounded-sm px-3 py-2 text-sm text-ink-text w-full";
export const btnCls = "text-sm font-medium border border-field rounded-sm px-3 py-1.5 text-ink-text bg-panel hover:bg-ink disabled:opacity-50";
export const primaryCls = "bg-info text-white font-semibold rounded-sm px-4 py-2 text-sm hover:brightness-110 disabled:opacity-60";

export function Section({ title, hint, children, action }: { title: string; hint?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-bold tracking-tight">{title}</h2>
          {hint && <p className="text-xs text-ink-muted mt-0.5 max-w-prose">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Card({ children, accent, className = "" }: { children: ReactNode; accent?: string; className?: string }) {
  return (
    <div className={`bg-panel border border-border rounded-sm p-5 ${className}`} style={accent ? { borderLeft: `4px solid ${accent}` } : undefined}>
      {children}
    </div>
  );
}

export function Stat({ label, value, delta, goodWhen = "up" }: { label: string; value: string; delta?: number | null; goodWhen?: "up" | "down" }) {
  const good = delta == null || delta === 0 ? null : (delta > 0) === (goodWhen === "up");
  return (
    <Card>
      <div className="text-xs text-ink-muted">{label}</div>
      <div className="kpi-figure text-2xl mt-1">{value}</div>
      {delta != null && (
        <div className="kpi-figure text-xs mt-1" style={{ color: good == null ? "var(--neutral)" : good ? "var(--good)" : "var(--bad)" }}>
          {delta > 0 ? "▲" : delta < 0 ? "▼" : "—"} {Math.abs(delta).toFixed(1)}%
        </div>
      )}
    </Card>
  );
}

/** Horizontal share bar: `value` and `max` in the same unit. Accessible via aria-label. */
export function Bar({ label, value, max, text, color = "#2f6db5" }: { label: string; value: number; max: number; text?: string; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="text-xs" aria-label={`${label}: ${text ?? value}`}>
      <div className="flex justify-between gap-3 mb-0.5">
        <span className="truncate text-ink-text">{label}</span>
        <span className="kpi-figure text-ink-muted shrink-0">{text ?? value}</span>
      </div>
      <div className="h-2 bg-ink rounded-full overflow-hidden"><div className="h-full" style={{ width: `${pct}%`, backgroundColor: color }} /></div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-ink-muted text-sm py-6 text-center border border-dashed border-border rounded-sm">{children}</div>;
}

export function Notice({ error, notice }: { error?: string | null; notice?: string | null }) {
  return (
    <>
      {error && <p role="alert" className="text-sm text-critical">{error}</p>}
      {notice && <p role="status" className="text-sm text-opportunity">{notice}</p>}
    </>
  );
}

export const money = (n: number, cur = "") => `${cur ? cur + " " : ""}${Math.round(n).toLocaleString("en-US")}`;
export const pct = (n: number | null | undefined, digits = 0) => (n == null ? "—" : `${(n * 100).toFixed(digits)}%`);
export const today = () => new Date().toISOString().slice(0, 10);

/** True once `active` has stayed true for `ms`. Free hosting sleeps when idle, so the first request can take a while. */
export function useSlow(active: boolean, ms = 3500) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!active) { setSlow(false); return; }
    const id = setTimeout(() => setSlow(true), ms);
    return () => clearTimeout(id);
  }, [active, ms]);
  return slow;
}

export function SlowHint() {
  return (
    <p role="status" className="text-sm text-ink-muted">
      Waking up the server. This can take up to a minute after a quiet spell. Your data is safe.
    </p>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}
