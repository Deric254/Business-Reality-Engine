import { useState } from "react";
import { Link } from "react-router-dom";
import { systemApi, type SystemLink } from "../api/client";
import { errText } from "../lib/errors";
import { TONE_COLOR, describeSystem } from "../lib/systemHelp";
import { useSystemLinks } from "../lib/useSystemLinks";
import { CopyBox, Live, Step } from "./Steps";
import { Card, btnCls, inputCls, primaryCls } from "./ui";

const found = (l: SystemLink) => Object.entries(l.last_pass?.rows ?? {}).map(([e, n]) => `${n.toLocaleString("en-US")} ${e}${n === 1 ? "" : "s"}`).join(", ");

/** Connect smePro, Imara and/or Pharmacy ERP. Tick the software on the computer, get ONE code, and the screen follows what the
 *  helper app does, so the owner sees it work instead of wondering. `start` opens one system straight at its steps (a new code). */
export function SoftwareFlow({ tenantId, start, onClose, onChanged }: { tenantId: string; start?: string; onClose: () => void; onChanged: () => void }) {
  const { links, reload, err } = useSystemLinks(tenantId, true);
  const [picked, setPicked] = useState<string[]>(start ? [start] : []);
  const [steps, setSteps] = useState(Boolean(start));
  const [code, setCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const mine = links.filter((l) => picked.includes(l.system));

  async function getCode() {
    setBusy(true); setProblem(null);
    try { setCode((await systemApi.connect(tenantId, picked)).code); setSteps(true); await reload(); onChanged(); } catch (e) { setProblem(errText(e)); } finally { setBusy(false); }
  }
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setProblem(null);
    try { await fn(); await reload(); onChanged(); } catch (e) { setProblem(errText(e)); } finally { setBusy(false); }
  }
  const toggle = (system: string) => setPicked((p) => (p.includes(system) ? p.filter((s) => s !== system) : [...p, system]));
  const again = () => { setPicked([]); setSteps(false); setCode(null); };

  // ---------- 1. which software ----------
  if (!steps || mine.length === 0) {
    return (
      <Card className="!p-6 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div><h2 className="display text-2xl">Which software do you use?</h2><p className="text-sm text-ink-muted mt-1">Tick every one that is on your computer. One code connects them all.</p></div>
          <button className={btnCls} onClick={onClose}>Back</button>
        </div>
        {(err || problem) && <p role="alert" className="text-sm text-critical">{problem ?? err}</p>}
        <div className="grid sm:grid-cols-3 gap-3">
          {links.map((l) => (
            <label key={l.system} className={`text-left border rounded-xl p-4 bg-panel flex gap-3 items-start ${l.connected ? "opacity-70" : "cursor-pointer hover:border-info"} ${picked.includes(l.system) ? "border-info bg-info-soft/40" : "border-border"}`}>
              <input type="checkbox" className="mt-1" checked={picked.includes(l.system)} disabled={l.connected} onChange={() => toggle(l.system)} aria-label={l.label} />
              <span>
                <span className="font-semibold block">{l.label}{l.connected && <span className="text-xs font-medium ml-2" style={{ color: TONE_COLOR.good }}>✓ Connected</span>}</span>
                <span className="text-sm text-ink-muted block mt-1">{l.what}</span>
              </span>
            </label>
          ))}
        </div>
        <div><button className={primaryCls} disabled={busy || picked.length === 0} onClick={getCode}>{busy ? "Getting your code…" : "Continue"}</button></div>
        <p className="text-sm text-ink-muted">Connecting uses a small <strong>helper app</strong> that runs on the computer where the software is. It only <em>reads</em> your data, safely, and sends it here. It never changes anything in your software.</p>
      </Card>
    );
  }

  const downloadUrl = links.find((l) => l.download_url)?.download_url ?? null;
  const names = mine.map((l) => l.label).join(" and ");

  // ---------- 3. it worked (not while a new code is being issued for a system that was already connected) ----------
  if (!start && mine.every((l) => l.connected && l.state === "connected")) {
    return (
      <Card className="!p-6 space-y-4" accent={TONE_COLOR.good}>
        <h2 className="display text-2xl">✓ {names} {mine.length === 1 ? "is" : "are"} connected</h2>
        <p className="text-ink-muted">{mine.map((l) => (found(l) ? `${l.label}: we found ${found(l)}.` : `${l.label}: we are bringing your data in.`)).join(" ")} Everything keeps itself up to date, so there is nothing more to do. If the computer is off, we'll tell you.</p>
        <div className="flex flex-wrap gap-2"><Link to="/" className={primaryCls}>See my overview</Link><button className={btnCls} onClick={again}>Connect another</button><button className={btnCls} onClick={onClose}>Done</button></div>
      </Card>
    );
  }

  // ---------- 2. three steps, with live status ----------
  const started = mine.some((l) => l.connected && l.state !== "waiting");
  return (
    <Card className="!p-6 space-y-5">
      <div className="flex items-start justify-between gap-3">
        <h2 className="display text-2xl">Connect {names}</h2>
        <div className="flex gap-2"><button className={btnCls} onClick={again}>Back</button><button className={btnCls} onClick={onClose}>Close</button></div>
      </div>
      {problem && <p role="alert" className="text-sm text-critical">{problem}</p>}
      <ol className="space-y-5">
        <Step n={1} title={`On the computer that has ${mine.length === 1 ? names : "your software"}, get the helper app`} done={started}>
          {downloadUrl
            ? <a className={primaryCls + " inline-block"} href={downloadUrl}>Download the helper app for Windows</a>
            : <p className="text-sm text-ink-muted">Ask the person who set up your system for the helper app (a file called <strong>RealityAgent.exe</strong>).</p>}
          <p className="text-sm text-ink-muted">Open it by double-clicking. A page opens in your browser.</p>
        </Step>
        <Step n={2} title="Paste this code on that page, then press Connect" done={started}>
          {code
            ? <><CopyBox value={code} label="code" /><p className="text-xs text-ink-faint">For your safety the code is shown only now. If you lose it, press Get a new code.</p></>
            : <button className={primaryCls} disabled={busy} onClick={getCode}>{busy ? "Getting your code…" : "Get a new code"}</button>}
        </Step>
        <Step n={3} title="Wait for the green tick" done={false}>
          <div className="space-y-3">
            {mine.map((l) => {
              const plain = describeSystem(l);
              const st = l.status ?? {};
              return (
                <div key={l.system} className="space-y-2">
                  <Live tone={plain.tone} title={mine.length > 1 ? `${l.label}: ${plain.title}` : plain.title}>
                    <p>{plain.todo}</p>
                    {l.detail && l.state !== "waiting" && <p className="text-xs">Details: {l.detail}</p>}
                  </Live>
                  {l.state === "needs_choice" && (st.scopes?.length ?? 0) > 0 && (
                    <label className="text-sm flex items-center gap-2">Business to read
                      <select className={inputCls + " max-w-xs"} defaultValue="" onChange={(e) => e.target.value && act(() => systemApi.settings(tenantId, l.connector_id!, { scope_id: e.target.value }))}>
                        <option value="" disabled>Choose…</option>
                        {st.scopes!.map((s) => <option key={s.id} value={s.id}>{s.name || s.id}</option>)}
                      </select>
                    </label>
                  )}
                  {l.state === "needs_confirmation" && <button className={primaryCls} disabled={busy} onClick={() => act(() => systemApi.confirm(tenantId, l.connector_id!))}>Confirm the change</button>}
                  {l.connected && l.state !== "waiting" && <button className={btnCls} disabled={busy || l.checking} onClick={() => act(() => systemApi.check(tenantId, l.connector_id!))}>{l.checking ? "Checking…" : `Check ${mine.length > 1 ? `${l.label} ` : ""}again`}</button>}
                </div>
              );
            })}
          </div>
        </Step>
      </ol>
      <p className="text-sm text-ink-muted">You can close this and come back: the page keeps watching. You only do this once per computer.</p>
    </Card>
  );
}
