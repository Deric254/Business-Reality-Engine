import type { Analytics, ChangeLine, Changes } from "../api/client";

/** The Analytics figures as CSV text: the same sections the page shows, plus what changed against the period before. */
export function csvOf(a: Analytics, cur: string, changes?: Changes | null): string {
  const q = (v: string | number | null) => (v == null ? "" : `"${String(v).replace(/"/g, '""')}"`);
  const lines: string[] = [];
  const sect = (title: string, head: string[], rows: (string | number | null)[][]) => { lines.push(q(title), head.map(q).join(","), ...rows.map((r) => r.map(q).join(",")), ""); };
  sect(`Summary (${a.period.first_day} to ${a.period.last_day}, ${cur})`, ["Measure", "This period", "Period before", "Change %"], [
    ["Sales", a.headline.sales.current, a.headline.sales.previous, a.headline.sales.change_pct], ["Spending", a.headline.spending.current, a.headline.spending.previous, a.headline.spending.change_pct],
    ["Profit", a.headline.profit.current, a.headline.profit.previous, a.headline.profit.change_pct], ["Margin %", a.headline.margin_pct.current, a.headline.margin_pct.previous, null],
    ["Average sale", a.headline.average_sale.current, a.headline.average_sale.previous, a.headline.average_sale.change_pct]]);
  sect("By month", ["Month", "Sales", "Spending", "Profit", "Margin %"], a.monthly.map((m) => [m.month.slice(0, 7), m.sales, m.spending, m.profit, m.margin_pct]));
  sect("By branch", ["Branch", "Sales", "Spending", "Profit", "Margin %", "Share of sales %", "Sales change %"], a.branches.map((b) => [b.name, b.sales, b.spending, b.profit, b.margin_pct, b.share_pct, b.sales_change_pct]));
  sect("Products", ["Product", "Sales", "Share %", "Cumulative %", "Change %"], a.products.top.map((p) => [p.name, p.amount, p.share_pct ?? null, p.cumulative_pct ?? null, p.change_pct]));
  sect("Spending by category", ["Category", "Amount", "Share %", "Period before", "Change %"], a.spending.categories.map((c) => [c.name, c.amount, c.share_pct ?? null, c.previous, c.change_pct]));
  sect("Customers", ["Customer", "Sales", "Orders", "Share %"], a.customers.top.map((c) => [c.name, c.amount, c.orders, c.share_pct]));
  if (changes !== undefined) {                                   // not passed: the file is exactly as it was before this section existed
    const title = "What changed (this period against the period before; spending going up counts as worse)";
    const note = changes === null ? "This section could not be prepared just now. Every other figure in this file is unaffected."
      : changes.note ?? (!changes.improved.length && !changes.worse.length ? "Nothing moved enough to report." : null);
    if (changes === null || note !== null) sect(title, ["Note"], [[note]]);
    else {
      const row = (direction: string, l: ChangeLine) => [direction, l.area, l.name, l.before, l.now, l.change, l.change_pct];
      sect(title, ["Direction", "Area", "Item", "Period before", "This period", "Change", "Change %"],
        [...changes.improved.map((l) => row("Getting better", l)), ...changes.worse.map((l) => row("Getting worse", l))]);
    }
  }
  return lines.join("\n");
}
