import { describe, expect, it } from "vitest";
import { columnLabel } from "./columns";

const info = { sample: [{ Date: "2026-10-01", "Unnamed: 2": "Sugar" }, { Date: "2026-10-02", "Unnamed: 2": "Oil" }] };

describe("columnLabel", () => {
  it("leaves a real name alone", () => expect(columnLabel("Amount", info)).toBe("Amount"));
  it("describes a header left blank by what its first cell holds", () => expect(columnLabel("Unnamed: 2", info)).toBe("(no name) for example “Sugar”"));
  it("still says it has no name when there is nothing to show", () => {
    expect(columnLabel("Unnamed: 7", info)).toBe("(no name)");
    expect(columnLabel("Unnamed: 2", null)).toBe("(no name)");
  });
  it("does not rename something that merely looks similar", () => expect(columnLabel("Unnamed", info)).toBe("Unnamed"));
});
