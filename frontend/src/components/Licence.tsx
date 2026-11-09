import { useEffect, useState } from "react";
import { LICENCE_READ_ONLY_EVENT, licenceApi, type MyLicence } from "../api/client";
import { APP_NAME, PAYPAL_ACCOUNT, noticeText, PAYPAL_URL, forgetProof, formatAmount, openAllowed, planFor, proofGmail, proofMailto, proofSentOn, rememberProof, type PayPlan } from "../lib/payWindow";
import { btnCls, primaryCls } from "./ui";

let askedOnce = false;                                  // a refused change opens the pay window the first time only; after that the notice alone answers it

/** The renewal notice shown in the page frame, and the pay window it opens. Never a lock screen: the page stays usable. */
export function LicenceNotice() {
  const [lic, setLic] = useState<MyLicence | null>(null);
  const [open, setOpen] = useState(false);
  const [refused, setRefused] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () => licenceApi.mine().then((l) => live && setLic(l)).catch(() => undefined);
    load();
    const onRefused = () => { load(); setRefused(true); if (!askedOnce) { askedOnce = true; setOpen(true); } };
    window.addEventListener(LICENCE_READ_ONLY_EVENT, onRefused);
    return () => { live = false; window.removeEventListener(LICENCE_READ_ONLY_EVENT, onRefused); };
  }, []);

  const text = lic && noticeText(lic);
  const plan = planFor(lic);
  if (!lic || !text || !plan) return null;
  return (
    <>
      <div role="status" className={`flex flex-wrap items-center justify-between gap-3 text-sm rounded-sm px-3 py-2 border ${lic.read_only || refused ? "text-critical bg-[#fef3f2] border-[#f4c7c2]" : "text-ink-text bg-panel border-field"}`}>
        <span>{text}</span>
        <button type="button" className={btnCls} onClick={() => setOpen(true)}>Renew</button>
      </div>
      {open && <PayWindow plan={plan} onClose={() => setOpen(false)} />}
    </>
  );
}

function CopyField({ label, value, hint }: { label: string; value: string; hint?: string }) {
  const [done, setDone] = useState(false);
  const copy = () => { navigator.clipboard?.writeText(value).then(() => setDone(true)).catch(() => undefined); };
  return (
    <div>
      <div className="text-xs text-ink-muted">{label}</div>
      <div className="flex items-center gap-2">
        <code className="text-sm break-all flex-1">{value}</code>
        <button type="button" className={btnCls} onClick={copy} aria-label={`Copy ${label}`}>{done ? "Copied" : "Copy"}</button>
      </div>
      {hint && <div className="text-xs text-ink-muted">{hint}</div>}
    </div>
  );
}

/** The one pay window: amount, PayPal account, reference, steps, and the proof email. Amount and currency are the licence's own. */
export function PayWindow({ plan, onClose }: { plan: PayPlan; onClose: () => void }) {
  const [sentOn, setSentOn] = useState<string | null>(() => proofSentOn(plan.reference));
  const sendProof = () => {
    if (openAllowed(proofMailto(plan))) { rememberProof(plan.reference); setSentOn(proofSentOn(plan.reference)); }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Renew your subscription" className="bg-panel rounded-sm border border-field max-w-md w-full max-h-full overflow-y-auto p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
        <div>
          <h2 className="font-semibold">Renew your {APP_NAME} subscription</h2>
          <div className="figure text-3xl mt-1">{formatAmount(plan.amount, plan.currency)} <span className="text-sm text-ink-muted">{plan.planLabel}</span></div>
        </div>
        <CopyField label="PayPal account" value={PAYPAL_ACCOUNT} />
        <CopyField label="Reference" value={plan.reference} hint="Put this in the PayPal note." />
        {sentOn ? (
          <div className="space-y-2">
            <p role="status" className="text-sm">Proof already sent on {new Date(sentOn).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}. Please do not pay again for the same thing. Access resumes as soon as the payment is recorded.</p>
            <button type="button" className={btnCls} onClick={() => { forgetProof(plan.reference); setSentOn(null); }}>I need to pay again</button>
          </div>
        ) : (
          <>
            <ol className="list-decimal pl-5 text-sm space-y-1">
              <li>Open PayPal and send exactly {formatAmount(plan.amount, plan.currency)} to {PAYPAL_ACCOUNT}, with the reference in the note.</li>
              <li>Take a screenshot showing the amount, the date and the transaction ID.</li>
              <li>Click "Email my proof", attach the screenshot and press Send. (Your email app cannot attach it for you.)</li>
            </ol>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={btnCls} onClick={() => openAllowed(PAYPAL_URL)}>Open PayPal</button>
              <button type="button" className={primaryCls} onClick={sendProof}>Email my proof</button>
            </div>
            <button type="button" className="text-xs underline text-ink-muted" onClick={() => openAllowed(proofGmail(plan))}>Write it in Gmail on the web</button>
          </>
        )}
        <div className="text-right"><button type="button" className={btnCls} onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
