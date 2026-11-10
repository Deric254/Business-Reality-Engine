import { useEffect, useState } from "react";
import { LICENCE_READ_ONLY_EVENT, licenceApi, type MyLicence } from "../api/client";
import { APP_NAME, PAYPAL_ACCOUNT, noticeText, PAYPAL_URL, forgetProof, formatAmount, openAllowed, planFor, prepareProof, proofSentOn, rememberProof, sendProof, type PayPlan, type ProofFile } from "../lib/payWindow";
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

const inputCls = "w-full border border-field rounded-sm px-2 py-1 text-sm bg-panel";

/** The one pay window: amount, PayPal account, reference, then the proof form. The screenshot is emailed to the owner by the server. */
export function PayWindow({ plan, onClose }: { plan: PayPlan; onClose: () => void }) {
  const [sentOn, setSentOn] = useState<string | null>(() => proofSentOn(plan.reference));
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [txn, setTxn] = useState("");
  const [trap, setTrap] = useState("");                       // hidden bot field: people never see it
  const [file, setFile] = useState<ProofFile | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const choose = async (f: File | undefined) => {
    setStatus("");
    if (!f) { setFile(null); return; }
    try { setFile(await prepareProof(f)); } catch { setFile(null); setStatus("That file is too large or is not an image or PDF. Please choose a screenshot."); }
  };
  const send = async () => {
    if (busy) return;
    if (!name.trim()) return setStatus("Please type your name.");
    if (!contact.trim()) return setStatus("Please add your phone or email so we can reply.");
    if (!file) return setStatus("Please choose your payment screenshot.");
    setBusy(true); setStatus("");
    const problem = await sendProof(plan, { name, contact, txn, website: trap, file });
    setBusy(false);
    if (problem) return setStatus(problem);
    rememberProof(plan.reference); setSentOn(proofSentOn(plan.reference)); setDone(true);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Renew your subscription" className="bg-panel rounded-sm border border-field max-w-md w-full max-h-full overflow-y-auto p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
        <div>
          <h2 className="font-semibold">Renew your {APP_NAME} subscription</h2>
          <div className="figure text-3xl mt-1">{formatAmount(plan.amount, plan.currency)} <span className="text-sm text-ink-muted">{plan.planLabel}</span></div>
        </div>
        {done ? (
          <p role="status" className="text-sm">Proof sent. Thank you, {name.trim()}. We will check your payment and reply to {contact.trim()}. Access resumes as soon as the payment is recorded.</p>
        ) : sentOn ? (
          <div className="space-y-2">
            <p role="status" className="text-sm">Proof already sent on {new Date(sentOn).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}. Please do not pay again for the same thing. Access resumes as soon as the payment is recorded.</p>
            <button type="button" className={btnCls} onClick={() => { forgetProof(plan.reference); setSentOn(null); }}>I need to pay again</button>
          </div>
        ) : (
          <>
            <ol className="list-decimal pl-5 text-sm space-y-1">
              <li>Send exactly {formatAmount(plan.amount, plan.currency)} to the PayPal account below, with the reference in the note.</li>
              <li>Take a screenshot showing the amount, the date and the transaction ID.</li>
              <li>Fill in the form and press "Send proof". We email it to the owner for you.</li>
            </ol>
            <CopyField label="PayPal account" value={PAYPAL_ACCOUNT} />
            <CopyField label="Reference" value={plan.reference} hint="Put this in the PayPal note." />
            <button type="button" className={btnCls} onClick={() => openAllowed(PAYPAL_URL)}>Open PayPal</button>
            <div className="space-y-2">
              <label className="block text-sm">Your name<input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" /></label>
              <label className="block text-sm">Your phone or email <span className="text-xs text-ink-muted">(so we can reply)</span><input className={inputCls} value={contact} onChange={(e) => setContact(e.target.value)} autoComplete="off" /></label>
              <label className="block text-sm">PayPal transaction ID <span className="text-xs text-ink-muted">(optional)</span><input className={inputCls} value={txn} onChange={(e) => setTxn(e.target.value)} autoComplete="off" /></label>
              <label className="block text-sm">Payment screenshot<input type="file" accept="image/*,application/pdf" className="block text-sm" onChange={(e) => choose(e.target.files?.[0])} /></label>
              {file?.type.startsWith("image/") && <img src={file.url} alt="Your screenshot" className="max-h-32 border border-field rounded-sm" />}
              <input type="text" name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" value={trap} onChange={(e) => setTrap(e.target.value)} style={{ position: "absolute", left: "-9999px", width: 1, height: 1, opacity: 0 }} />
            </div>
            {status && <p role="alert" className="text-sm text-critical">{status}</p>}
            <button type="button" className={primaryCls} onClick={send} disabled={busy}>{busy ? "Sending…" : "Send proof"}</button>
          </>
        )}
        <div className="text-right"><button type="button" className={btnCls} onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
