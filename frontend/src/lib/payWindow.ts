import type { MyLicence } from "../api/client";

/** The one pay window's fixed facts. The amount and currency are never here: they come from the business's own licence. */
export const PAYPAL_ACCOUNT = "dericmarangu@gmail.com";
export const APP_TAG = "BRE";
export const APP_NAME = "Business Reality Engine";
export const PAYPAL_URL = "https://www.paypal.com/myaccount/transfer/homepage";

/** The only links the app opens outside itself. */
export const ALLOWED_LINKS = ["https://www.paypal.com/", "https://mail.google.com/mail/?view=cm", `mailto:${PAYPAL_ACCOUNT}?`];

export const isAllowedLink = (url: string) => ALLOWED_LINKS.some((p) => url.startsWith(p));

/** Opens an allowlisted link in a new window; anything else is ignored. */
export function openAllowed(url: string): boolean {
  if (!isAllowedLink(url)) return false;
  window.open(url, "_blank", "noopener,noreferrer");
  return true;
}

export interface PayPlan { licenceId: string; reference: string; amount: number; currency: string; months: number; planLabel: string }

/** Everything the window shows, from the licence record alone. null when this licence has nothing to pay for. */
export function planFor(l: MyLicence | null): PayPlan | null {
  if (!l?.payable || !l.licence_id || l.amount_cents == null || !l.currency) return null;
  const months = l.interval_months || 1;
  return { licenceId: l.licence_id, reference: `LIC-${APP_TAG}-${l.licence_id}-${months === 1 ? "M" : `M${months}`}`, amount: l.amount_cents / 100, currency: l.currency,
           months, planLabel: months === 1 ? "monthly" : `${months}-month` };
}

export function formatAmount(amount: number, currency: string): string {
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount); } catch { return `${amount} ${currency}`; }
}

const mail = (p: PayPlan) => ({
  subject: `Payment proof - ${APP_NAME} licence ${p.planLabel} - ${p.reference}`,
  body: [`I paid for: ${APP_NAME} subscription (${p.planLabel})`, `Amount: ${formatAmount(p.amount, p.currency)}`, `Reference: ${p.reference}`,
         "PayPal transaction ID: ", "My name: ", "", "(Please attach your PayPal screenshot before sending.)"].join("\n"),
});

export function proofMailto(p: PayPlan): string {
  const m = mail(p);
  return `mailto:${PAYPAL_ACCOUNT}?subject=${encodeURIComponent(m.subject)}&body=${encodeURIComponent(m.body)}`;
}

export function proofGmail(p: PayPlan): string {
  const m = mail(p);
  return `https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(PAYPAL_ACCOUNT)}&su=${encodeURIComponent(m.subject)}&body=${encodeURIComponent(m.body)}`;
}

// "Proof already sent" is remembered in this browser only, per reference, so a customer is not asked to pay twice.
const proofKey = (reference: string) => `bre.proof.${reference}`;
export function proofSentOn(reference: string): string | null {
  try { return localStorage.getItem(proofKey(reference)); } catch { return null; }
}
export function rememberProof(reference: string, when = new Date()): void {
  try { localStorage.setItem(proofKey(reference), when.toISOString()); } catch { /* private window: the reminder is a convenience only */ }
}
export function forgetProof(reference: string): void {
  try { localStorage.removeItem(proofKey(reference)); } catch { /* nothing remembered */ }
}

const WARN_DAYS = 14;                                   // matches the server: a renewal notice when this many days or fewer are left
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** What to tell the business about its subscription, or null when nothing needs saying. */
export function noticeText(l: MyLicence): string | null {
  if (!l.payable) return null;
  if (l.read_only) return "Your subscription has expired, so this is view-only. You can still read and export your data. Renew to add or change anything.";
  if (l.grace) return `Subscription expired. Renew within ${plural(l.grace_days_left ?? 0, "day")} to keep full access.`;
  if ((l.days_left ?? WARN_DAYS + 1) <= WARN_DAYS) return `Your subscription ends in ${plural(l.days_left ?? 0, "day")}. Renew to keep full access.`;
  return null;
}
