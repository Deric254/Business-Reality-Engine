import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Inspection } from "../api/client";
import { FoundPanel } from "./FoundPanel";

const base: Inspection = {
  columns: ["Date", "Item", "Amount"], row_count: 1204, sample: [{ Date: "2026-10-01", Item: "Sugar", Amount: 500 }], suggested_mapping: {}, source: "rules", reasons: {}, guessed: [],
  quality: { blank_rows: 0, blank_columns: [], unnamed_columns: [], unreadable_dates: 0, unreadable_amounts: 0, rows_without_amount: 0, kind: "transactions" },
};
const text = () => within(screen.getByLabelText("What we found")).getByRole("list").textContent ?? "";
const mapped = { timestamp_col: "Date", amount_col: "Amount" };

describe("What we found", () => {
  it("says what it read and nothing alarming when the file is clean", () => {
    render(<FoundPanel info={base} mapping={mapped} />);
    expect(text()).toBe("✓Read 1,204 rows and 3 columns.");
  });

  it("says out loud what it ignored and what it could not read", () => {
    const quality = { ...base.quality, blank_rows: 12, blank_columns: ["a", "b"], unnamed_columns: ["Unnamed: 3"], unreadable_dates: 1, unreadable_amounts: 4, rows_without_amount: 2 };
    render(<FoundPanel info={{ ...base, quality }} mapping={mapped} />);
    const t = text();
    for (const part of ["Ignored 12 empty rows.", "Ignored 2 empty columns.", "1 column has data but no name", "1 row has a date we can't read", "4 amounts can't be read as a number", "2 rows have no amount"]) expect(t).toContain(part);
  });

  it("names what it only guessed, so the owner checks it", () => {
    render(<FoundPanel info={{ ...base, guessed: ["amount_col"] }} mapping={mapped} />);
    expect(text()).toContain("We guessed the amount column from what is in it");
  });

  it("tells the owner when there is no date, when there is no amount, and when the file is only a list", () => {
    const { rerender } = render(<FoundPanel info={base} mapping={{ amount_col: "Amount" }} />);
    expect(text()).toContain("no date column");
    rerender(<FoundPanel info={base} mapping={{ timestamp_col: "Date" }} />);
    expect(text()).toContain("No amount column was found");
    rerender(<FoundPanel info={base} mapping={{}} />);
    expect(text()).toContain("looks like a list");
  });

  it("shows a column left without a name as (no name), not as 'Unnamed: 2'", () => {
    render(<FoundPanel info={{ ...base, columns: ["Date", "Unnamed: 2"], sample: [{ Date: "x", "Unnamed: 2": "Sugar" }] }} mapping={mapped} />);
    expect(screen.getByRole("columnheader", { name: "(no name)" })).toBeInTheDocument();
    expect(screen.queryByText(/Unnamed/)).toBeNull();
  });
});
