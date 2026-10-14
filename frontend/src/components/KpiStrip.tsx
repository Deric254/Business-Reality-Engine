interface Trend { direction: "up" | "down" | "flat"; pct: number; goodDirection: "up" | "down" }

function KpiCell({ label, value, trend, dot }: { label: string; value: string; trend?: Trend; dot?: string }) {
  const isGood = trend && trend.direction === trend.goodDirection;
  const isBad = trend && trend.direction !== "flat" && trend.direction !== trend.goodDirection;
  return (
    <div className="px-4 sm:px-5 py-4 min-w-0">
      <div className="flex items-center gap-2 text-sm text-ink-muted">
        {dot && <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: dot }} aria-hidden />}
        {label}
      </div>
      <div className="kpi-figure text-xl sm:text-2xl text-ink-text mt-1 break-words">{value}</div>
      {trend && trend.direction !== "flat" && (
        <div className="text-sm mt-0.5 kpi-figure" style={{ color: isGood ? "var(--good)" : isBad ? "var(--bad)" : "var(--neutral)" }}>
          {trend.direction === "up" ? "▲" : "▼"} {trend.pct.toFixed(1)}% <span className="font-normal text-ink-muted">vs before</span>
        </div>
      )}
      {trend && trend.direction === "flat" && <div className="text-sm mt-0.5 text-ink-muted">No change</div>}
    </div>
  );
}

interface KpiStripProps {
  revenue: { current: number; previous: number };
  profit: { current: number; previous: number; change_pct: number | null };
  criticalCount: number;
  highCount: number;
  largestContributor: string | null;
  largestContributorPct: number | null;
}

function pctTrend(current: number, previous: number): { direction: "up" | "down" | "flat"; pct: number } {
  if (previous === 0) return { direction: "flat", pct: 0 };
  const pct = ((current - previous) / previous) * 100;
  if (Math.abs(pct) < 0.5) return { direction: "flat", pct: 0 };
  return { direction: pct > 0 ? "up" : "down", pct: Math.abs(pct) };
}

const fmt = (n: number) => new Intl.NumberFormat("en-KE", { maximumFractionDigits: 0 }).format(n);

export function KpiStrip({ revenue, profit, criticalCount, highCount, largestContributor, largestContributorPct }: KpiStripProps) {
  const revenueTrend = pctTrend(revenue.current, revenue.previous);
  const profitTrend = profit.change_pct !== null
    ? { direction: (profit.change_pct > 0.5 ? "up" : profit.change_pct < -0.5 ? "down" : "flat") as "up" | "down" | "flat", pct: Math.abs(profit.change_pct) }
    : { direction: "flat" as const, pct: 0 };

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 bg-panel border border-border rounded-sm divide-border divide-x divide-y lg:divide-y-0 overflow-hidden">
      <KpiCell label="Revenue" value={fmt(revenue.current)} trend={{ ...revenueTrend, goodDirection: "up" }} />
      <KpiCell label="Profit" value={fmt(profit.current)} trend={{ ...profitTrend, goodDirection: "up" }} />
      <KpiCell
        label="Open alerts"
        value={`${criticalCount} critical · ${highCount} high`}
        dot={criticalCount > 0 ? "#d92d20" : highCount > 0 ? "#e8590c" : "#1f9254"}
      />
      <KpiCell
        label="Biggest change driver"
        value={largestContributor || "None yet"}
        trend={largestContributorPct !== null ? { direction: largestContributorPct >= 0 ? "up" : "down", pct: Math.abs(largestContributorPct), goodDirection: "down" } : undefined}
      />
    </div>
  );
}
