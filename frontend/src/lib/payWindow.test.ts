import { afterEach, describe, expect, it, vi } from "vitest";
import type { MyLicence } from "../api/client";
import { ALLOWED_LINKS, PAYPAL_ACCOUNT, forgetProof, isAllowedLink, openAllowed, planFor, proofGmail, proofMailto, proofSentOn, rememberProof } from "./payWindow";

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
  it("sends proof to the one PayPal account with the reference, amount and an honest attach note", () => {
    const p = planFor(LIC)!;
    const url = proofMailto(p);
    expect(url.startsWith(`mailto:${PAYPAL_ACCOUNT}?`)).toBe(true);
    const body = decodeURIComponent(url.split("body=")[1]);
    expect(body).toContain("LIC-BRE-abc-123-M");
    expect(body).toContain("PayPal transaction ID: ");
    expect(body).toContain("My name: ");
    expect(body).toContain("Please attach your PayPal screenshot");
    expect(decodeURIComponent(url.split("subject=")[1].split("&")[0])).toBe("Payment proof - Business Reality Engine licence monthly - LIC-BRE-abc-123-M");
    expect(proofGmail(p).startsWith("https://mail.google.com/mail/?view=cm&fs=1&to=dericmarangu%40gmail.com&su=")).toBe(true);
  });
});

describe("link allowlist", () => {
  it("opens only allowlisted links, without giving the new window access to the app", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    expect(ALLOWED_LINKS).toHaveLength(3);
    expect(openAllowed("https://www.paypal.com/myaccount/transfer/homepage")).toBe(true);
    expect(open).toHaveBeenCalledWith("https://www.paypal.com/myaccount/transfer/homepage", "_blank", "noopener,noreferrer");
    for (const bad of ["https://evil.example/", "https://www.paypal.com.evil.example/", "javascript:alert(1)", "mailto:someone@else.com?x=1", "https://mail.google.com/mail/u/0/"]) {
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
