import { describe, expect, it } from "vitest";
import type { Analytics, ChangeLine, Changes } from "../api/client";
import { csvOf } from "./analyticsCsv";

const metric = (current: number, previous: number | null = null) => ({ current, previous, change_pct: null });
const A = {
  period: { first_day: "2026-08-01", last_day: "2026-10-24" },
  headline: { sales: metric(100, 90), spending: metric(40, 30), profit: metric(60, 60), average_sale: metric(10, 9), margin_pct: { current: 60, previous: 66 } },
  monthly: [], branches: [], products: { top: [] }, spending: { categories: [] }, customers: { top: [] },
} as unknown as Analytics;

const line = (area: string, name: string, kind: ChangeLine["kind"], before: number, now: number, pct: number | null): ChangeLine =>
  ({ area, name, kind, before, now, change: now - before, change_pct: pct, detail: "" });
const CHANGES = { period: {}, days: 90, improved: [line("Business", "Sales", "improved", 90, 100, 11.1)], worse: [line("Branch", 'Meru "North"', "worse", 30, 20, -33.3)] } as unknown as Changes;

describe("csvOf: what changed", () => {
  it("lists what got better and worse, with the figures and quotes escaped", () => {
    const out = csvOf(A, "KES", CHANGES);
    expect(out).toContain('"What changed (this period against the period before; spending going up counts as worse)"');
    expect(out).toContain('"Getting better","Business","Sales","90","100","10","11.1"');
    expect(out).toContain('"Getting worse","Branch","Meru ""North""","30","20","-10","-33.3"');
  });
  it("leaves the section out when it is not passed, so older callers get the file as before", () => {
    expect(csvOf(A, "KES")).not.toContain("What changed");
  });
  it("says so in the file when the figures could not be fetched, and when there is nothing to compare", () => {
    expect(csvOf(A, "KES", null)).toContain("could not be prepared just now");
    expect(csvOf(A, "KES", { ...CHANGES, improved: [], worse: [], note: "All time has nothing earlier to compare with." } as Changes)).toContain("All time has nothing earlier to compare with.");
    expect(csvOf(A, "KES", { ...CHANGES, improved: [], worse: [] } as Changes)).toContain("Nothing moved enough to report.");
  });
  it("keeps every other section", () => {
    expect(csvOf(A, "KES", CHANGES)).toContain('"Summary (2026-08-01 to 2026-10-24, KES)"');
  });
});
