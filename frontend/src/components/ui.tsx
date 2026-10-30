import { useEffect, useState, type ReactNode } from "react";

export const inputCls = "bg-panel border border-field rounded-sm px-3 py-2 text-sm text-ink-text w-full placeholder:text-ink-faint";
export const btnCls = "text-sm font-medium border border-field rounded-sm px-3.5 py-1.5 max-lg:py-2.5 text-ink-text bg-panel hover:bg-panel-raised disabled:opacity-50 transition-colors";
export const primaryCls = "bg-info text-white font-semibold rounded-sm px-4 py-2 max-lg:py-2.5 text-sm hover:brightness-110 disabled:opacity-60 transition";

export function Section({ title, hint, children, action }: { title: string; hint?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          {hint && <p className="text-sm text-ink-muted mt-0.5 max-w-prose">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function PageTitle({ title, sub, action }: { title: string; sub?: string; action?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="display text-3xl sm:text-4xl">{title}</h1>
        {sub && <p className="text-ink-muted mt-1.5 max-w-prose">{sub}</p>}
      </div>
      {action}
    </header>
  );
}

export function Card({ children, accent, className = "" }: { children: ReactNode; accent?: string; className?: string }) {
  return (
    <div className={`bg-panel border border-border rounded-xl p-5 ${className}`} style={accent ? { borderLeft: `4px solid ${accent}` } : undefined}>
      {children}
    </div>
  );
}

export function Stat({ label, value, delta, goodWhen = "up" }: { label: string; value: string; delta?: number | null; goodWhen?: "up" | "down" }) {
  const good = delta == null || delta === 0 ? null : (delta > 0) === (goodWhen === "up");
  return (
    <Card>
      <div className="text-sm text-ink-muted">{label}</div>
      <div className="figure text-3xl mt-1">{value}</div>
      {delta != null && (
        <div className="kpi-figure text-sm mt-1" style={{ color: good == null ? "var(--neutral)" : good ? "var(--good)" : "var(--bad)" }}>
          {delta > 0 ? "Up" : delta < 0 ? "Down" : "No change"} {delta !== 0 && `${Math.abs(delta).toFixed(1)}%`}
        </div>
      )}
    </Card>
  );
}

/** Horizontal share bar: `value` and `max` in the same unit. Accessible via aria-label. */
export function Bar({ label, value, max, text, color = "#1b5a54" }: { label: string; value: number; max: number; text?: string; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="text-sm" aria-label={`${label}: ${text ?? value}`}>
      <div className="flex justify-between gap-3 mb-1">
        <span className="truncate text-ink-text">{label}</span>
        <span className="kpi-figure text-ink-muted shrink-0">{text ?? value}</span>
      </div>
      <div className="h-2 bg-ink rounded-full overflow-hidden"><div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} /></div>
    </div>
  );
}

export function Empty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="text-ink-muted text-sm py-8 px-4 text-center border border-dashed border-field rounded-xl bg-panel/60 space-y-3">
      <div>{children}</div>
      {action}
    </div>
  );
}

export function Notice({ error, notice }: { error?: string | null; notice?: string | null }) {
  return (
    <>
      {error && <p role="alert" className="text-sm text-critical bg-[#fef3f2] border border-[#f4c7c2] rounded-sm px-3 py-2">{error}</p>}
      {notice && <p role="status" className="text-sm text-opportunity bg-[#edf8f1] border border-[#bfe3cd] rounded-sm px-3 py-2">{notice}</p>}
    </>
  );
}

export const money = (n: number, cur = "") => `${cur ? cur + " " : ""}${Math.round(n).toLocaleString("en-US")}`;
export const pct = (n: number | null | undefined, digits = 0) => (n == null ? "—" : `${(n * 100).toFixed(digits)}%`);
export const today = () => new Date().toISOString().slice(0, 10);
export const compact = (n: number) => new Intl.NumberFormat("en-KE", { notation: "compact", maximumFractionDigits: 1 }).format(n);

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
      The server is waking up. This can take up to a minute after a quiet spell. Your data is safe.
    </p>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}
