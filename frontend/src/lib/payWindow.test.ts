import { afterEach, describe, expect, it, vi } from "vitest";
import type { MyLicence } from "../api/client";
import { ALLOWED_LINKS, forgetProof, isAllowedLink, openAllowed, planFor, proofBody, proofSentOn, rememberProof, sendProof } from "./payWindow";

const LIC: MyLicence = { ok: true, kind: "licensed", read_only: false, grace: false, payable: true, licence_id: "abc-123", amount_cents: 3700, currency: "KES", interval_months: 1 };

afterEach(() => vi.restoreAllMocks());

describe("pay window facts", () => {
  it("takes amount, currency and reference from the tenant's own licence", () => {
    expect(planFor(LIC)).toMatchObject({ amount: 37, currency: "KES", reference: "LIC-BRE-abc-123-M", planLabel: "monthly" });
    expect(planFor({ ...LIC, amount_cents: 12000, currency: "USD", interval_months: 3 })).toMatchObject({ amount: 120, currency: "USD", reference: "LIC-BRE-abc-123-M3" });
  });
  it("has nothing to pay for on a free licence or before the licence is known", () => {
    expect(planFor({ ok: true, kind: "free", read_only: false, grace: false, payable: false })).toBeNull();
    expect(planFor(null)).toBeNull();
  });
  it("builds the proof body from the licence, never from the form", () => {
    const b = proofBody(planFor(LIC)!, { name: " Amina ", contact: "0712", txn: "", website: "", file: { type: "image/png", data: "AAAA", url: "data:" } });
    expect(b).toMatchObject({ item: "Business Reality Engine subscription (monthly)", amount: 37, currency: "KES", qty: 1, name: "Amina", extraLabel: "Reference", extra: "LIC-BRE-abc-123-M", file: { type: "image/png", data: "AAAA" } });
  });
  it("sends to /api/payment-proof and turns each answer into plain words", async () => {
    const f = { name: "A", contact: "c", txn: "", website: "", file: { type: "image/png", data: "AAAA", url: "" } };
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200 });
    expect(await sendProof(planFor(LIC)!, f)).toBeNull();
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/payment-proof$/);
    for (const [status, text] of [[429, /Too many/], [413, /too large/], [503, /not switched on/], [400, /missing/], [502, /could not send/]] as const) {
      fetchMock.mockResolvedValueOnce({ ok: false, status });
      expect(await sendProof(planFor(LIC)!, f)).toMatch(text);
    }
    fetchMock.mockRejectedValueOnce(new Error("offline"));
    expect(await sendProof(planFor(LIC)!, f)).toMatch(/No internet/);
    vi.unstubAllGlobals();
  });
});

describe("link allowlist", () => {
  it("opens only allowlisted links, without giving the new window access to the app", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    expect(ALLOWED_LINKS).toHaveLength(1);
    expect(openAllowed("https://www.paypal.com/myaccount/transfer/homepage")).toBe(true);
    expect(open).toHaveBeenCalledWith("https://www.paypal.com/myaccount/transfer/homepage", "_blank", "noopener,noreferrer");
    for (const bad of ["https://evil.example/", "https://www.paypal.com.evil.example/", "javascript:alert(1)", "mailto:dericmarangu@gmail.com?x=1", "https://mail.google.com/mail/?view=cm"]) {
      expect(isAllowedLink(bad)).toBe(false);
      expect(openAllowed(bad)).toBe(false);
    }
    expect(open).toHaveBeenCalledTimes(1);
  });
});

describe("proof already sent", () => {
  it("is remembered per reference and can be cleared", () => {
    expect(proofSentOn("LIC-BRE-a-M")).toBeNull();
    rememberProof("LIC-BRE-a-M", new Date("2026-11-08T10:00:00Z"));
    expect(proofSentOn("LIC-BRE-a-M")).toBe("2026-11-08T10:00:00.000Z");
    expect(proofSentOn("LIC-BRE-b-M")).toBeNull();
    forgetProof("LIC-BRE-a-M");
    expect(proofSentOn("LIC-BRE-a-M")).toBeNull();
  });
  it("works without storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(proofSentOn("x")).toBeNull();
    expect(() => rememberProof("x")).not.toThrow();
  });
});
