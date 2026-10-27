import { afterEach, describe, expect, it } from "vitest";
import { getLang, setLang, translate } from "./i18n";

afterEach(() => setLang("en"));

describe("translations", () => {
  it("fill every {placeholder} in both languages", () => {
    for (const lang of ["en", "sw"] as const) {
      setLang(lang);
      expect(getLang()).toBe(lang);
      expect(translate("biz.days", { n: 7 })).toMatch(/7/);
      expect(translate("biz.days", { n: 7 })).not.toMatch(/[{}]/);
      expect(translate("wiz.step", { n: 2, total: 5 })).toMatch(/2.*5/);
      expect(translate("dash.critical.many", { n: 3 })).not.toMatch(/[{}]/);
    }
  });

  it("the sign-in options are written in Swahili too, not shown as keys or as English", () => {
    setLang("sw");
    const remember = translate("login.remember");
    expect(remember).not.toBe("login.remember");
    expect(remember).not.toBe((setLang("en"), translate("login.remember")));
  });

  it("the main navigation is translated, and switching back restores English", () => {
    setLang("en");
    const english = translate("nav.overview");
    setLang("sw");
    expect(translate("nav.overview")).not.toBe(english);
    expect(translate("nav.overview")).not.toBe("nav.overview");
    setLang("en");
    expect(translate("nav.overview")).toBe(english);
  });
});
