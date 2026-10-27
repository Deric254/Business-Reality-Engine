import { beforeEach, describe, expect, it } from "vitest";
import { getClaims, getTokens, isRemembered, isSignedIn, setTokens } from "./session";

const pair = (n: string) => ({ access_token: `a-${n}`, refresh_token: `r-${n}` });

beforeEach(() => setTokens(null));

describe("session storage", () => {
  it("keeps a normal sign-in in this tab only", () => {
    setTokens(pair("1"));
    expect(sessionStorage.getItem("bre.refresh")).toBe("r-1");
    expect(localStorage.getItem("bre.refresh")).toBeNull();
    expect(isRemembered()).toBe(false);
  });

  it("keeps a remembered sign-in on the device, and nowhere else", () => {
    setTokens(pair("1"), true);
    expect(localStorage.getItem("bre.refresh")).toBe("r-1");
    expect(sessionStorage.getItem("bre.refresh")).toBeNull();
    expect(isRemembered()).toBe(true);
  });

  it("keeps the person's choice when tokens are refreshed", () => {
    setTokens(pair("1"), true);
    setTokens(pair("2"));                                   // a refresh gives no choice
    expect(localStorage.getItem("bre.refresh")).toBe("r-2");
    expect(sessionStorage.getItem("bre.refresh")).toBeNull();
    setTokens(null);
    setTokens(pair("3"));                                   // a new, ordinary sign-in is not remembered
    expect(isRemembered()).toBe(false);
  });

  it("finds a remembered sign-in after the page is reloaded, and signing out clears every copy", () => {
    localStorage.setItem("bre.access", "a-x"); localStorage.setItem("bre.refresh", "r-x");
    expect(getTokens()).toEqual({ access_token: "a-x", refresh_token: "r-x" });
    expect(isSignedIn()).toBe(true);
    setTokens(null);
    expect(getTokens()).toBeNull();
    expect(localStorage.length + sessionStorage.length).toBe(0);
  });

  it("reads the role from the token for display only, and survives a token that is not one", () => {
    const body = btoa(JSON.stringify({ role: "owner", mcp: true })).replace(/=+$/, "");
    setTokens({ access_token: `h.${body}.s`, refresh_token: "r" });
    expect(getClaims()).toEqual({ role: "owner", mustChangePassword: true });
    setTokens({ access_token: "garbage", refresh_token: "r" });
    expect(getClaims()).toEqual({ role: null, mustChangePassword: false });
  });
});
