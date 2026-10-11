interface KpiCardProps {
  label: string;
  value: string;
  trend?: { direction: "up" | "down" | "flat"; pct: number; goodDirection: "up" | "down" };
  accent?: string;
}

function KpiCard({ label, value, trend, accent }: KpiCardProps) {
  const isGood = trend && trend.direction === trend.goodDirection;
  const isBad = trend && trend.direction !== "flat" && trend.direction !== trend.goodDirection;

  return (
    <div
      className="bg-panel border-l-2 pl-4 py-3 pr-4 flex-1 min-w-[160px]"
      style={{ borderLeftColor: accent || "var(--color-border)" }}
    >
      <div className="text-xs text-ink-muted mb-1">{label}</div>
      <div className="kpi-figure text-2xl text-ink-text">{value}</div>
      {trend && trend.direction !== "flat" && (
        <div
          className="text-xs mt-1 kpi-figure"
          style={{ color: isGood ? "#4caf7d" : isBad ? "#e5484d" : "#8b93a1" }}
        >
          {trend.direction === "up" ? "▲" : "▼"} {trend.pct.toFixed(1)}%
        </div>
      )}
      {trend && trend.direction === "flat" && (
        <div className="text-xs mt-1 text-ink-faint kpi-figure">— flat</div>
      )}
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

export function KpiStrip({ revenue, profit, criticalCount, highCount, largestContributor, largestContributorPct }: KpiStripProps) {
  const revenueTrend = pctTrend(revenue.current, revenue.previous);
  const profitTrend = profit.change_pct !== null
    ? { direction: (profit.change_pct > 0.5 ? "up" : profit.change_pct < -0.5 ? "down" : "flat") as "up" | "down" | "flat", pct: Math.abs(profit.change_pct) }
    : { direction: "flat" as const, pct: 0 };

  return (
    <div className="flex gap-px bg-border mb-6 flex-wrap">
      <KpiCard
        label="Revenue"
        value={new Intl.NumberFormat("en-KE", { maximumFractionDigits: 0 }).format(revenue.current)}
        trend={{ ...revenueTrend, goodDirection: "up" }}
        accent="#5b9bd5"
      />
      <KpiCard
        label="Profit"
        value={new Intl.NumberFormat("en-KE", { maximumFractionDigits: 0 }).format(profit.current)}
        trend={{ ...profitTrend, goodDirection: "up" }}
        accent={profitTrend.direction === "down" ? "#e5484d" : "#4caf7d"}
      />
      <KpiCard
        label="Open alerts"
        value={`${criticalCount} crit, ${highCount} high`}
        accent={criticalCount > 0 ? "#e5484d" : highCount > 0 ? "#f0883e" : "#4caf7d"}
      />
      <KpiCard
        label="Largest driver"
        value={largestContributor || "—"}
        trend={
          largestContributorPct !== null
            ? { direction: largestContributorPct >= 0 ? "up" : "down", pct: Math.abs(largestContributorPct), goodDirection: "down" }
            : undefined
        }
        accent="#f0883e"
      />
    </div>
  );
}
