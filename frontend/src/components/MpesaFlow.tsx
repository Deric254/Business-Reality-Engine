import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE, connectorApi, tenantApi, type ConnectorRow, type TestEventResult } from "../api/client";
import { errText } from "../lib/errors";
import { TONE_COLOR } from "../lib/systemHelp";
import { CopyBox, Live, Step } from "./Steps";
import { Card, btnCls, primaryCls } from "./ui";

const fullLink = (path: string) => `${API_BASE || window.location.origin}${path}`;
const when = (iso: string) => new Date(iso).toLocaleString("en-KE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** M-Pesa payments, as each one happens. Three steps: make the link, give it to Safaricom, check it. The screen proves the
 *  link works (without storing anything) and then waits for the first real payment, so the owner is never left guessing. */
export function MpesaFlow({ tenantId, existing, onClose, onChanged }: { tenantId: string; existing?: ConnectorRow; onClose: () => void; onChanged: () => void }) {
  const [connector, setConnector] = useState<ConnectorRow | null>(existing ?? null);
  const [link, setLink] = useState<string | null>(null);          // known only right after it is made or replaced: it is shown once
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [test, setTest] = useState<TestEventResult["would_create"] | null>(null);
  const [payments, setPayments] = useState(0);

  const refresh = useCallback(async () => {
    if (!connector) return;
    try { setPayments((await connectorApi.contributions(tenantId))[connector.id]?.counted ?? 0); } catch { /* the next look will do */ }
  }, [connector, tenantId]);
  useEffect(() => { if (!connector) return; const first = setTimeout(refresh, 0); const t = setInterval(refresh, 4000); return () => { clearTimeout(first); clearInterval(t); }; }, [connector, refresh]);

  async function create() {
    setBusy(true); setProblem(null);
    try {
      const me = await tenantApi.me();
      const made = await connectorApi.create({ tenant_id: tenantId, name: "M-Pesa payments", connector_type: "webhook", config: { preset: "mpesa_c2b", default_currency: me.base_currency }, expected_frequency: "realtime" });
      setConnector(made); setLink(made.webhook_url ? fullLink(made.webhook_url) : null); onChanged();
    } catch (e) { setProblem(errText(e)); } finally { setBusy(false); }
  }
  async function replace() {
    if (!connector) return;
    setBusy(true); setProblem(null);
    try { setLink(fullLink((await connectorApi.newLink(tenantId, connector.id)).webhook_url)); setTest(null); } catch (e) { setProblem(errText(e)); } finally { setBusy(false); }
  }
  async function runTest() {
    if (!connector) return;
    setBusy(true); setProblem(null);
    try { setTest((await connectorApi.testEvent(tenantId, connector.id)).would_create); } catch (e) { setProblem(errText(e)); } finally { setBusy(false); }
  }

  // ---------- before anything exists ----------
  if (!connector) {
    return (
      <Card className="!p-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div><h2 className="display text-2xl">See every M-Pesa payment as it happens</h2><p className="text-sm text-ink-muted mt-1 max-w-prose">When a customer pays your Paybill or Till number, Safaricom tells us, and it appears here as a sale. No typing, no files.</p></div>
          <button className={btnCls} onClick={onClose}>Back</button>
        </div>
        {problem && <p role="alert" className="text-sm text-critical">{problem}</p>}
        <button className={primaryCls} disabled={busy} onClick={create}>{busy ? "Creating…" : "Create my M-Pesa link"}</button>
        <p className="text-sm text-ink-muted"><strong>Good to know:</strong> this needs a Paybill or Till number that Safaricom can send payments from (through their Daraja service). If you don't have that, go back and choose <em>A spreadsheet or file</em>: you can upload your M-Pesa statement today, with no setup.</p>
      </Card>
    );
  }

  // ---------- the first real payment has arrived ----------
  if (payments > 0) {
    return (
      <Card className="!p-6 space-y-4" accent={TONE_COLOR.good}>
        <h2 className="display text-2xl">✓ Your first M-Pesa payment arrived</h2>
        <p className="text-ink-muted">{payments.toLocaleString("en-US")} payment{payments === 1 ? "" : "s"} so far. Each new one appears here as it happens.</p>
        <div className="flex flex-wrap gap-2"><Link to="/" className={primaryCls}>See my overview</Link><button className={btnCls} onClick={onClose}>Done</button></div>
      </Card>
    );
  }

  // ---------- the three steps ----------
  return (
    <Card className="!p-6 space-y-5">
      <div className="flex items-start justify-between gap-3">
        <h2 className="display text-2xl">Set up M-Pesa payments</h2>
        <button className={btnCls} onClick={onClose}>Close</button>
      </div>
      {problem && <p role="alert" className="text-sm text-critical">{problem}</p>}
      <ol className="space-y-5">
        <Step n={1} title="Your M-Pesa link" done={link != null}>
          {link
            ? <><CopyBox value={link} label="link" /><p className="text-xs text-ink-faint">Keep this page open until you have given it to Safaricom. For your safety it is shown only now; if you lose it, you can make a new one (the old one then stops working).</p></>
            : <><p className="text-sm text-ink-muted">For your safety the link is shown only once, when it is made. To see one again, make a new link; the old one then stops working.</p><button className={btnCls} disabled={busy} onClick={replace}>{busy ? "Making a new link…" : "Make a new link"}</button></>}
        </Step>
        <Step n={2} title="Give the link to Safaricom">
          <p className="text-sm text-ink-muted">Ask Safaricom, through the Daraja portal or your Safaricom business contact, to use this link as the <strong>confirmation link</strong> for your Paybill or Till number.</p>
          <details className="text-sm text-ink-muted"><summary className="cursor-pointer">What does Safaricom do with it?</summary>
            <p className="mt-1.5">Each time a customer pays your number, Safaricom sends the payment (who paid, how much, when) to this link. We turn it into a sale and show the payer's phone number in place of a name. Only successful payments are sent. If the same payment arrives twice, it is counted once.</p></details>
        </Step>
        <Step n={3} title="Check that it works" done={test != null}>
          <p className="text-sm text-ink-muted">This shows what a sample payment would look like here. It does not add anything to your figures.</p>
          <button className={btnCls} disabled={busy} onClick={runTest}>{busy ? "Testing…" : "Test my link"}</button>
          {test && <Live tone="good" title="It works"><p>A payment of {test.currency ?? ""} {(test.amount ?? 0).toLocaleString("en-US")}{test.customer_id ? ` from ${test.customer_id}` : ""} would appear as a sale on {when(test.time)}.</p></Live>}
          <Live tone="wait" title="Waiting for your first real payment…"><p>This page notices the moment one arrives. You can close it and come back; nothing is lost.</p></Live>
        </Step>
      </ol>
    </Card>
  );
}
