import type { ReactNode } from "react";

export const inputCls = "bg-ink border border-border rounded-sm px-2 py-1.5 text-sm text-ink-text w-full";
export const btnCls = "text-xs border border-border rounded-sm px-2.5 py-1.5 text-ink-muted hover:text-ink-text disabled:opacity-50";
export const primaryCls = "bg-info text-ink font-medium rounded-sm px-3 py-1.5 text-sm disabled:opacity-60";

export function Section({ title, hint, children, action }: { title: string; hint?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
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
    <div className={`bg-panel border border-border rounded-sm p-4 ${className}`} style={accent ? { borderLeft: `2px solid ${accent}` } : undefined}>
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
        <div className="kpi-figure text-xs mt-1" style={{ color: good == null ? "#8b93a1" : good ? "#4caf7d" : "#e5484d" }}>
          {delta > 0 ? "▲" : delta < 0 ? "▼" : "—"} {Math.abs(delta).toFixed(1)}%
        </div>
      )}
    </Card>
  );
}

/** Horizontal share bar: `value` and `max` in the same unit. Accessible via aria-label. */
export function Bar({ label, value, max, text, color = "#5b9bd5" }: { label: string; value: number; max: number; text?: string; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="text-xs" aria-label={`${label}: ${text ?? value}`}>
      <div className="flex justify-between gap-3 mb-0.5">
        <span className="truncate text-ink-text">{label}</span>
        <span className="kpi-figure text-ink-muted shrink-0">{text ?? value}</span>
      </div>
      <div className="h-1.5 bg-ink rounded-sm overflow-hidden"><div className="h-full" style={{ width: `${pct}%`, backgroundColor: color }} /></div>
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
