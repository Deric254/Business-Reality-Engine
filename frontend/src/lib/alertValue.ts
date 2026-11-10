import type { AlertValue } from "../api/client";

const money = (n: number, cur: string) => `${cur} ${Math.round(n).toLocaleString("en-US")}`.trim();

/** One quiet line about what the open alerts add up to, or null when there is nothing worth saying (so the page shows nothing).
 *  It states a size, never a saving: an alert is money off its usual, not money the owner is promised back. */
export function valueLine(v: AlertValue | null | undefined): string | null {
  if (!v) return null;
  const parts: string[] = [];
  if (v.open_at_stake > 0) parts.push(`About ${money(v.open_at_stake, v.currency)} off your usual across ${v.open_measured} open alert${v.open_measured === 1 ? "" : "s"}`);
  if (v.back_to_normal > 0) parts.push(`${v.back_to_normal} went back to normal in the last ${v.days} days`);
  return parts.length ? parts.join(" · ") : null;
}
