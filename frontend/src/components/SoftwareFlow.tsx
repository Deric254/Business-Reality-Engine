import { useState } from "react";
import { Link } from "react-router-dom";
import { systemApi, type SystemLink } from "../api/client";
import { errText } from "../lib/errors";
import { TONE_COLOR, describeSystem } from "../lib/systemHelp";
import { useSystemLinks } from "../lib/useSystemLinks";
import { CopyBox, Live, Step } from "./Steps";
import { Card, btnCls, inputCls, primaryCls } from "./ui";

/** Connect smePro, Imara or Pharmacy ERP. Pick the software, then three plain steps, and the screen follows what the helper
 *  app does, so the owner sees it work instead of wondering. */
export function SoftwareFlow({ tenantId, start, onClose, onChanged }: { tenantId: string; start?: string; onClose: () => void; onChanged: () => void }) {
  const { links, reload, err } = useSystemLinks(tenantId, true);
  const [system, setSystem] = useState<string | null>(start ?? null);
  const [code, setCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const link = links.find((l) => l.system === system) ?? null;

  async function getCode(sys: string) {
    setBusy(true); setProblem(null);
    try { setCode((await systemApi.connect(tenantId, sys)).code); await reload(); onChanged(); } catch (e) { setProblem(errText(e)); } finally { setBusy(false); }
  }
  function choose(l: SystemLink) {
    setSystem(l.system); setCode(null);
    if (!l.connected) void getCode(l.system);          // a new system gets its code straight away; one that is already connected shows how it is doing
  }
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setProblem(null);
    try { await fn(); await reload(); onChanged(); } catch (e) { setProblem(errText(e)); } finally { setBusy(false); }
  }

  // ---------- 1. which software ----------
  if (!system || !link) {
    return (
      <Card className="!p-6 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div><h2 className="display text-2xl">Which software do you use?</h2><p className="text-sm text-ink-muted mt-1">Pick the one on your computer. You can connect more than one, one after the other.</p></div>
          <button className={btnCls} onClick={onClose}>Back</button>
        </div>
        {err && <p role="alert" className="text-sm text-critical">{err}</p>}
        <div className="grid sm:grid-cols-3 gap-3">
          {links.map((l) => (
            <button key={l.system} onClick={() => choose(l)} className="text-left border border-border rounded-xl p-4 bg-panel hover:border-info hover:bg-info-soft/40 transition-colors">
              <div className="font-semibold">{l.label}{l.connected && <span className="text-xs font-medium ml-2" style={{ color: TONE_COLOR.good }}>✓ Connected</span>}</div>
              <div className="text-sm text-ink-muted mt-1">{l.what}</div>
            </button>
          ))}
        </div>
        <p className="text-sm text-ink-muted">Connecting uses a small <strong>helper app</strong> that runs on the computer where the software is. It only <em>reads</em> your data, safely, and sends it here. It never changes anything in your software.</p>
      </Card>
    );
  }

  const plain = describeSystem(link);
  const downloadUrl = link.download_url ?? links.find((l) => l.download_url)?.download_url ?? null;

  // ---------- 3. it worked ----------
  if (link.connected && link.state === "connected") {
    const rows = Object.entries(link.last_pass?.rows ?? {}).map(([e, n]) => `${n.toLocaleString("en-US")} ${e}${n === 1 ? "" : "s"}`).join(", ");
    return (
      <Card className="!p-6 space-y-4" accent={TONE_COLOR.good}>
        <h2 className="display text-2xl">✓ {link.label} is connected</h2>
        <p className="text-ink-muted">{rows ? `We found ${rows}.` : "We are bringing your data in."} It keeps itself up to date, so there is nothing more to do. If the computer is off, we'll tell you.</p>
        <div className="flex flex-wrap gap-2"><Link to="/" className={primaryCls}>See my overview</Link><button className={btnCls} onClick={() => { setSystem(null); setCode(null); }}>Connect another</button><button className={btnCls} onClick={onClose}>Done</button></div>
      </Card>
    );
  }

  // ---------- 2. three steps, with live status ----------
  const st = link.status ?? {};
  return (
    <Card className="!p-6 space-y-5">
      <div className="flex items-start justify-between gap-3">
        <h2 className="display text-2xl">Connect {link.label}</h2>
        <div className="flex gap-2"><button className={btnCls} onClick={() => { setSystem(null); setCode(null); }}>Back</button><button className={btnCls} onClick={onClose}>Close</button></div>
      </div>
      {problem && <p role="alert" className="text-sm text-critical">{problem}</p>}
      <ol className="space-y-5">
        <Step n={1} title={`On the computer that has ${link.label}, get the helper app`} done={link.connected && link.state !== "waiting"}>
          {downloadUrl
            ? <a className={primaryCls + " inline-block"} href={downloadUrl}>Download the helper app for Windows</a>
            : <p className="text-sm text-ink-muted">Ask the person who set up your system for the helper app (a file called <strong>RealityAgent.exe</strong>).</p>}
          <p className="text-sm text-ink-muted">Open it by double-clicking. A page opens in your browser.</p>
        </Step>
        <Step n={2} title="Paste this code on that page, then press Connect" done={link.connected && link.state !== "waiting"}>
          {code
            ? <><CopyBox value={code} label="code" /><p className="text-xs text-ink-faint">For your safety the code is shown only now. If you lose it, press Get a new code.</p></>
            : <button className={primaryCls} disabled={busy} onClick={() => getCode(link.system)}>{busy ? "Getting your code…" : "Get a new code"}</button>}
        </Step>
        <Step n={3} title="Wait for the green tick" done={false}>
          <Live tone={plain.tone} title={plain.title}>
            <p>{plain.todo}</p>
            {link.detail && link.state !== "waiting" && <p className="text-xs">Details: {link.detail}</p>}
          </Live>
          {link.state === "needs_choice" && (st.scopes?.length ?? 0) > 0 && (
            <label className="text-sm flex items-center gap-2">Business to read
              <select className={inputCls + " max-w-xs"} defaultValue="" onChange={(e) => e.target.value && act(() => systemApi.settings(tenantId, link.connector_id!, { scope_id: e.target.value }))}>
                <option value="" disabled>Choose…</option>
                {st.scopes!.map((s) => <option key={s.id} value={s.id}>{s.name || s.id}</option>)}
              </select>
            </label>
          )}
          {link.state === "needs_confirmation" && <button className={primaryCls} disabled={busy} onClick={() => act(() => systemApi.confirm(tenantId, link.connector_id!))}>Confirm the change</button>}
          {link.connected && link.state !== "waiting" && <button className={btnCls} disabled={busy || link.checking} onClick={() => act(() => systemApi.check(tenantId, link.connector_id!))}>{link.checking ? "Checking…" : "Check again"}</button>}
        </Step>
      </ol>
      <p className="text-sm text-ink-muted">You can close this and come back: the page keeps watching. You only do this once per computer.</p>
    </Card>
  );
}
