import { describe, expect, it } from "vitest";
import { describeSystem, overallHealth } from "./systemHelp";

const link = (state: string, connected = true, paused = false) => ({ label: "smePro", state, connected, paused });
// every state the server can report (app/core/systems.py)
const STATES = ["waiting", "connected", "offline", "not_found", "not_this_system", "needs_driver", "needs_key", "locked", "unreadable", "schema_changed", "needs_choice", "needs_confirmation", "refused"];

describe("describeSystem", () => {
  it("has plain words and a next step for every state the server can report", () => {
    for (const s of STATES) {
      const p = describeSystem(link(s));
      expect(p.title.length, s).toBeGreaterThan(3);
      expect(p.todo.length, s).toBeGreaterThan(10);
    }
  });

  it("names the software in the sentences that need it, and avoids the jargon", () => {
    expect(describeSystem(link("not_found")).title).toContain("smePro");
    const all = STATES.map((s) => JSON.stringify(describeSystem(link(s)))).join(" ");
    for (const jargon of ["agent", "scope", "schema", "token", "sqlcipher"]) expect(all.toLowerCase()).not.toContain(jargon);
  });

  it("calls only a green link good, and a state it does not know is just 'checking'", () => {
    expect(describeSystem(link("connected")).tone).toBe("good");
    expect(describeSystem(link("not_found")).tone).toBe("bad");
    expect(describeSystem(link("waiting")).tone).toBe("wait");
    expect(describeSystem(link("something_new")).title).toBe("Checking…");
    expect(describeSystem(link("connected", false)).title).toBe("Not connected yet");
  });
});

describe("overallHealth", () => {
  it("says nothing when nothing is connected", () => {
    expect(overallHealth([], 0, 0)).toBeNull();
  });
  it("counts what needs attention before what is still being set up, and counts sources too", () => {
    expect(overallHealth([link("connected"), link("not_found")], 0, 1)).toEqual({ tone: "bad", text: "1 of your 3 connections needs attention." });
    expect(overallHealth([link("waiting")], 0, 0)).toEqual({ tone: "wait", text: "1 connection is still being set up." });
    expect(overallHealth([link("connected")], 2, 3)).toEqual({ tone: "bad", text: "2 of your 4 connections need attention." });
  });
  it("is good only when everything works, and a paused system is not a problem", () => {
    expect(overallHealth([link("connected")], 0, 0)?.text).toBe("Your connection is working.");
    expect(overallHealth([link("connected"), link("not_found", true, true)], 0, 1)?.tone).toBe("good");
    expect(overallHealth([link("connected", false)], 0, 2)?.text).toBe("All 2 of your connections are working.");
  });
});
