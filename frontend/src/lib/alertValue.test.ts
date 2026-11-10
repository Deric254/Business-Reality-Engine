import { describe, expect, it } from "vitest";
import type { AlertValue } from "../api/client";
import { valueLine } from "./alertValue";

const V: AlertValue = { days: 30, open_count: 4, open_measured: 2, open_at_stake: 46250.4, raised: 6, raised_at_stake: 90000, back_to_normal: 3, currency: "KES" };

describe("valueLine", () => {
  it("says how much the open alerts add up to and how many went back to normal", () => {
    expect(valueLine(V)).toBe("About KES 46,250 off your usual across 2 open alerts · 3 went back to normal in the last 30 days");
  });
  it("uses the singular for one alert", () => {
    expect(valueLine({ ...V, open_measured: 1, back_to_normal: 0 })).toBe("About KES 46,250 off your usual across 1 open alert");
  });
  it("shows nothing at all when there is nothing to say, so the page stays clean", () => {
    expect(valueLine(null)).toBeNull();
    expect(valueLine(undefined)).toBeNull();
    expect(valueLine({ ...V, open_at_stake: 0, back_to_normal: 0 })).toBeNull();
  });
  it("can say the good news alone", () => {
    expect(valueLine({ ...V, open_at_stake: 0, open_measured: 0 })).toBe("3 went back to normal in the last 30 days");
  });
  it("never shows a negative or empty amount", () => {
    expect(valueLine({ ...V, open_at_stake: -5, back_to_normal: 0 })).toBeNull();
  });
});
