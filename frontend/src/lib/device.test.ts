import { describe, expect, it } from "vitest";
import { isPhoneOrTablet } from "./device";

const ua = (userAgent: string, maxTouchPoints = 0) => ({ userAgent, maxTouchPoints });

describe("isPhoneOrTablet", () => {
  it("is true on Android and iPhone", () => {
    expect(isPhoneOrTablet(ua("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36"))).toBe(true);
    expect(isPhoneOrTablet(ua("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"))).toBe(true);
  });
  it("is true on an iPad, including one that reports itself as a Mac", () => {
    expect(isPhoneOrTablet(ua("Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15"))).toBe(true);
    expect(isPhoneOrTablet(ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15", 5))).toBe(true);
  });
  it("is false on Windows (touch screens included), a real Mac and Linux, which can all be given the helper app", () => {
    expect(isPhoneOrTablet(ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36", 10))).toBe(false);
    expect(isPhoneOrTablet(ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15", 0))).toBe(false);
    expect(isPhoneOrTablet(ua("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"))).toBe(false);
  });
  it("is false when there is no browser to ask", () => {
    expect(isPhoneOrTablet(undefined)).toBe(false);
  });
});
