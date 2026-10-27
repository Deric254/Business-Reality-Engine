import { describe, expect, it } from "vitest";
import { importSummary, leftOutNote, skippedNote } from "./client";

describe("what an import says it did", () => {
  it("says nothing extra when nothing was skipped or left out", () => {
    expect(skippedNote({})).toBe("");
    expect(leftOutNote({})).toBe("");
  });

  it("names rows left out on request, with how much money they were", () => {
    expect(leftOutNote({ left_out: 2, left_out_total: 2500 })).toContain("2 money-out rows (2,500 in all) left out of spending");
    expect(leftOutNote({ left_out: 1, left_out_total: 40 })).toContain("1 money-out row (40 in all)");
  });

  it("puts added, unchanged, skipped and left-out rows into one plain sentence", () => {
    const text = importSummary({ rows_processed: 5, new_rows: 3, duplicates: 2, skipped: 1, skipped_examples: ["Row 9: not a date"], left_out: 2, left_out_total: 2500 }, "Uploaded");
    expect(text).toContain("3 added");
    expect(text).toContain("2 unchanged");
    expect(text).toContain("1 row skipped (Row 9: not a date)");
    expect(text).toContain("2 money-out rows");
  });
});
