import { API_BASE, type MyLicence } from "../api/client";

/** The one pay window's fixed facts. The amount and currency are never here: they come from the business's own licence. */
export const PAYPAL_ACCOUNT = "dericmarangu@gmail.com";
export const APP_TAG = "BRE";
export const APP_NAME = "Business Reality Engine";
export const PAYPAL_URL = "https://www.paypal.com/myaccount/transfer/homepage";

/** The only link the app opens outside itself. */
export const ALLOWED_LINKS = ["https://www.paypal.com/"];

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

// ---- the payment proof: same flow as the owner's other sites. The customer uploads a PayPal screenshot here and the server
// emails it to the owner as a real attachment (POST /api/payment-proof).
const MAX_SIDE = 1600;                                   // screenshots are shrunk to this many pixels on the longest side
const MAX_ORIGINAL = 2.5 * 1024 * 1024;
export interface ProofFile { type: string; data: string; url: string }

function readBase64(blob: Blob): Promise<ProofFile> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => { const s = String(r.result); resolve({ type: blob.type, data: s.slice(s.indexOf(",") + 1), url: s }); };
    r.onerror = () => reject(new Error("read"));
    r.readAsDataURL(blob);
  });
}

async function shrink(f: File): Promise<Blob> {
  const bmp = await createImageBitmap(f);
  const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(bmp.width * scale));
  c.height = Math.max(1, Math.round(bmp.height * scale));
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("canvas");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.drawImage(bmp, 0, 0, c.width, c.height);
  bmp.close?.();
  return new Promise((resolve, reject) => c.toBlob((b) => (b ? resolve(b) : reject(new Error("blob"))), "image/jpeg", 0.82));
}

/** Turns the chosen screenshot into what the server accepts: a shrunk JPEG, or the original PNG/JPEG/WebP/PDF when small enough. */
export async function prepareProof(f: File): Promise<ProofFile> {
  if (f.type === "application/pdf") {
    if (f.size > MAX_ORIGINAL) throw new Error("big");
    return readBase64(f);
  }
  const plain = /^image\/(jpeg|png|webp)$/.test(f.type) && f.size <= MAX_ORIGINAL;
  try {
    if (typeof createImageBitmap !== "function") throw new Error("nobitmap");
    return await readBase64(await shrink(f));
  } catch {
    if (plain) return readBase64(f);
    throw new Error("type");
  }
}

export interface ProofForm { name: string; contact: string; txn: string; website: string; file: ProofFile }

/** What the server needs. Item, amount and currency are the licence's own; the reference rides along as the extra detail. */
export function proofBody(p: PayPlan, f: ProofForm) {
  return { item: `${APP_NAME} subscription (${p.planLabel})`, amount: p.amount, currency: p.currency, qty: 1, name: f.name.trim(), contact: f.contact.trim(),
           txn: f.txn.trim(), extraLabel: "Reference", extra: p.reference, website: f.website, file: { type: f.file.type, data: f.file.data } };
}

/** Sends the proof; resolves to null when sent, or a plain-language reason when not. */
export async function sendProof(p: PayPlan, f: ProofForm): Promise<string | null> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}/api/payment-proof`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(proofBody(p, f)) });
  } catch { return "No internet connection. Please check it and try again."; }
  if (r.ok) return null;
  if (r.status === 429) return "Too many tries. Please wait a few minutes and try again.";
  if (r.status === 413) return "That screenshot is too large. Please choose a smaller one.";
  if (r.status === 503) return "Sending proof is not switched on yet. Please contact the service provider.";
  if (r.status === 400) return "Something is missing or the file is not an image or PDF. Please check and try again.";
  return "We could not send your proof. Please try again.";
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
