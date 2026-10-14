// Minimal i18n: English and Swahili. A missing key falls back to English, then to the key itself, so a
// partially translated screen is never blank. The choice is remembered per browser (storage may throw).
import { useSyncExternalStore } from "react";

export type Lang = "en" | "sw";
const KEY = "bre.lang";

const en = {
  "nav.alerts": "Alerts", "nav.business": "Business", "nav.data": "Data sources", "nav.accuracy": "Accuracy",
  "nav.settings": "Settings", "nav.signout": "Sign out", "lang.label": "Language",
  "login.title": "Sign in to Business Reality", "login.email": "Email", "login.password": "Password",
  "login.submit": "Sign in", "login.forgot": "Forgot your password?",
  "forgot.title": "Reset your password", "forgot.help": "Enter your account email and we'll send a reset link.",
  "forgot.send": "Send reset link", "forgot.sent": "If that address has an account, a reset link is on its way.",
  "reset.title": "Choose a new password", "invite.title": "Welcome - set your password",
  "reset.new": "New password", "reset.again": "Repeat new password", "reset.submit": "Save password",
  "reset.done": "Password saved. You can sign in now.", "back.signin": "Back to sign in",
  "biz.title": "Your business at a glance", "biz.health": "Business health", "biz.sales": "Sales", "biz.expenses": "Expenses",
  "biz.profit": "Profit", "biz.days": "Last {n} days", "biz.quality": "Data quality", "biz.discovery": "What we found in your data",
  "biz.relationships": "Who and what is connected", "biz.context": "Events that affect your numbers", "biz.fx": "Exchange rates",
  "biz.stale": "Sources not reporting", "biz.openAlerts": "Open alerts",
  "data.title": "Data sources", "data.add": "Connect a source", "data.capture": "Add an entry by hand",
  "acc.title": "Accuracy and model", "acc.backtest": "Test against history", "acc.truth": "Known incidents",
  "acc.changes": "Business changes", "acc.report": "How useful the alerts were",
  "wiz.title": "Let's get your business set up", "wiz.step": "Step {n} of {total}",
  "common.save": "Save", "common.add": "Add", "common.remove": "Remove", "common.cancel": "Cancel", "common.loading": "Loading…",
  "common.next": "Next", "common.back": "Back", "common.skip": "Skip for now", "common.done": "Finish",
  "common.none": "Nothing here yet.",
  "dash.allClear": "Nothing needs your attention right now.",
  "dash.critical.one": "1 critical alert needs you today.", "dash.critical.many": "{n} critical alerts need you today.",
  "dash.high.one": "1 alert needs a look today.", "dash.high.many": "{n} alerts need a look today.",
  "dash.sub": "Last 30 days, compared with the 30 before.",
} as const;

const sw: Partial<Record<keyof typeof en, string>> = {
  "nav.alerts": "Tahadhari", "nav.business": "Biashara", "nav.data": "Vyanzo vya data", "nav.accuracy": "Usahihi",
  "nav.settings": "Mipangilio", "nav.signout": "Toka", "lang.label": "Lugha",
  "login.title": "Ingia kwenye Business Reality", "login.email": "Barua pepe", "login.password": "Nenosiri",
  "login.submit": "Ingia", "login.forgot": "Umesahau nenosiri?",
  "forgot.title": "Weka upya nenosiri lako", "forgot.help": "Weka barua pepe ya akaunti yako tutakutumia kiungo cha kuweka upya.",
  "forgot.send": "Tuma kiungo", "forgot.sent": "Ikiwa anwani hiyo ina akaunti, kiungo cha kuweka upya kinakuja.",
  "reset.title": "Chagua nenosiri jipya", "invite.title": "Karibu - weka nenosiri lako",
  "reset.new": "Nenosiri jipya", "reset.again": "Rudia nenosiri jipya", "reset.submit": "Hifadhi nenosiri",
  "reset.done": "Nenosiri limehifadhiwa. Sasa unaweza kuingia.", "back.signin": "Rudi kuingia",
  "biz.title": "Biashara yako kwa muhtasari", "biz.health": "Afya ya biashara", "biz.sales": "Mauzo", "biz.expenses": "Matumizi",
  "biz.profit": "Faida", "biz.days": "Siku {n} zilizopita", "biz.quality": "Ubora wa data", "biz.discovery": "Tulichokipata kwenye data yako",
  "biz.relationships": "Nani na nini vimeunganishwa", "biz.context": "Matukio yanayoathiri namba zako", "biz.fx": "Viwango vya kubadilisha fedha",
  "biz.stale": "Vyanzo visivyotuma data", "biz.openAlerts": "Tahadhari zilizo wazi",
  "data.title": "Vyanzo vya data", "data.add": "Unganisha chanzo", "data.capture": "Ongeza rekodi kwa mkono",
  "acc.title": "Usahihi na modeli", "acc.backtest": "Jaribu dhidi ya historia", "acc.truth": "Matukio yanayojulikana",
  "acc.changes": "Mabadiliko ya biashara", "acc.report": "Tahadhari zilikuwa na manufaa kiasi gani",
  "wiz.title": "Tuandae biashara yako", "wiz.step": "Hatua {n} kati ya {total}",
  "common.save": "Hifadhi", "common.add": "Ongeza", "common.remove": "Ondoa", "common.cancel": "Ghairi", "common.loading": "Inapakia…",
  "common.next": "Endelea", "common.back": "Rudi", "common.skip": "Ruka kwa sasa", "common.done": "Maliza",
  "common.none": "Hakuna kitu bado.",
  "dash.allClear": "Hakuna kinachohitaji umakini wako sasa hivi.",
  "dash.critical.one": "Tahadhari 1 kubwa inakuhitaji leo.", "dash.critical.many": "Tahadhari {n} kubwa zinakuhitaji leo.",
  "dash.high.one": "Tahadhari 1 inahitaji kutazamwa leo.", "dash.high.many": "Tahadhari {n} zinahitaji kutazamwa leo.",
  "dash.sub": "Siku 30 zilizopita, ikilinganishwa na siku 30 kabla yake.",
};

export type TKey = keyof typeof en;
const dict: Record<Lang, Partial<Record<TKey, string>>> = { en, sw };

let current: Lang = "en";
try {
  const saved = localStorage.getItem(KEY);
  if (saved === "en" || saved === "sw") current = saved;
} catch { /* storage unavailable: stay on English */ }
const listeners = new Set<() => void>();

export function setLang(l: Lang) {
  current = l;
  try { localStorage.setItem(KEY, l); } catch { /* ignore */ }
  listeners.forEach((f) => f());
}
export const getLang = (): Lang => current;

export function translate(key: TKey, vars?: Record<string, string | number>): string {
  let s = dict[current][key] ?? dict.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, String(v));
  return s;
}

export function useT() {
  useSyncExternalStore((fn) => { listeners.add(fn); return () => listeners.delete(fn); }, getLang);
  return translate;
}
