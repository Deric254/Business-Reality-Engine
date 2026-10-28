import { useCallback, useEffect, useState } from "react";
import { ApiError, systemApi, type SystemLink } from "../api/client";
import { Notice, btnCls, inputCls, primaryCls } from "./ui";
import { timeAgo } from "../lib/severity";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");
const GOOD = "#1b8a5a", WARN = "#d4a017", BAD = "#c0392b", IDLE = "#8a8f98";

function tone(l: SystemLink): string {
  if (!l.connected) return IDLE;
  if (l.state === "connected") return l.in_sync ? GOOD : WARN;
  if (l.state === "waiting" || l.state === "locked" || l.state === "needs_choice" || l.state === "needs_confirmation") return WARN;
  return BAD;
}

function Tile({ link, tenantId, isOwner, reload, onCode, setError, setNotice }: {
  link: SystemLink; tenantId: string; isOwner: boolean; reload: () => void; onCode: (system: string, code: string) => void; setError: (s: string | null) => void; setNotice: (s: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [path, setPath] = useState("");
  const id = link.connector_id;
  async function act(fn: () => Promise<unknown>, done?: string) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); if (done) setNotice(done); reload(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }
  const st = link.status ?? {};
  const rows = link.last_pass?.rows ?? {};
  const counts = Object.entries(rows).map(([e, n]) => `${n.toLocaleString()} ${e}${n === 1 ? "" : "s"}`).join(", ");
  const choosing = link.state === "needs_choice" && (st.scopes?.length ?? 0) > 0;
  return (
    <li className="bg-panel border border-border rounded-xl px-4 sm:px-5 py-4 flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="font-semibold">{link.label}</div>
          <div className="text-sm text-ink-muted">{link.what}</div>
        </div>
        <span className="text-sm flex items-center gap-2" role="status">
          <span className="w-2 h-2 rounded-full" style={{ background: tone(link) }} aria-hidden />
          {link.connected ? (link.checking ? "Checking…" : link.paused ? "Paused" : link.headline) : "Not connected"}
        </span>
      </div>
      {link.connected && link.detail && link.state !== "connected" && <div className="text-sm" role="alert">{link.detail}</div>}
      {link.connected && link.state === "connected" && counts && <div className="text-sm text-ink-muted">{counts}{link.last_synced_at ? `, in step ${timeAgo(link.last_synced_at)}` : ""}</div>}
      {link.connected && link.verify && link.verify.match === false && <div className="text-sm text-ink-muted">The engine's copy differed from the system and is being repaired.</div>}
      {link.notice && <div className="text-sm text-ink-muted">{link.notice}</div>}
      {link.connected && st.path && <div className="text-xs text-ink-muted break-all">Database: {st.path} ({st.path_source})</div>}
      {link.connected && st.scope_name && <div className="text-xs text-ink-muted">Business: {st.scope_name}</div>}
      {link.connected && !st.path && (st.searched?.length ?? 0) > 0 && <details className="text-xs text-ink-muted"><summary className="cursor-pointer">Where it looked</summary><ul className="mt-1 break-all">{st.searched!.map((p) => <li key={p}>{p}</li>)}</ul></details>}
      {link.connected && link.last_seen_at && <div className="text-xs text-ink-muted">Agent last reported {timeAgo(link.last_seen_at)}</div>}
      {link.connected && link.state === "waiting" && <div className="text-sm text-ink-muted">Waiting for the agent. On the computer that has {link.label}, open the agent and paste the code you were given.</div>}
      {choosing && isOwner && (
        <label className="text-sm flex items-center gap-2">Business to read
          <select className={inputCls + " max-w-xs"} defaultValue="" onChange={(e) => e.target.value && act(() => systemApi.settings(tenantId, id!, { scope_id: e.target.value }))}>
            <option value="" disabled>Choose…</option>
            {st.scopes!.map((s) => <option key={s.id} value={s.id}>{s.name || s.id}</option>)}
          </select>
        </label>
      )}
      {editing && isOwner && (
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); act(() => systemApi.settings(tenantId, id!, { path: path.trim() }), path.trim() ? "Location saved. Checking it now." : "Back to finding it automatically.").then(() => setEditing(false)); }}>
          <input className={inputCls + " flex-1 min-w-[240px]"} placeholder="Full path of the database file (leave empty to find it automatically)" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Database location" />
          <button className={primaryCls} disabled={busy}>Save</button><button type="button" className={btnCls} onClick={() => setEditing(false)}>Cancel</button>
        </form>
      )}
      <div className="flex flex-wrap gap-2 mt-1">
        {!link.connected && isOwner && <button className={primaryCls} disabled={busy} onClick={() => act(async () => { const r = await systemApi.connect(tenantId, link.system); onCode(link.system, r.code); })}>Connect</button>}
        {link.connected && <button className={btnCls} disabled={busy || link.checking} onClick={() => act(() => systemApi.check(tenantId, id!))}>{link.checking ? "Checking…" : "Check now"}</button>}
        {link.connected && link.state === "needs_confirmation" && isOwner && <button className={primaryCls} disabled={busy} onClick={() => act(() => systemApi.confirm(tenantId, id!), "Confirmed. The engine will mirror the system now.")}>Confirm this change</button>}
        {link.connected && isOwner && <button className={btnCls} onClick={() => { setEditing(!editing); setPath(st.path_source === "set by hand" ? st.path ?? "" : ""); }}>Set location</button>}
        {link.connected && isOwner && <button className={btnCls} disabled={busy} title="Compares everything and repairs any difference" onClick={() => act(() => systemApi.resync(tenantId, id!), "Re-checking everything.")}>Re-verify all</button>}
        {link.connected && isOwner && <button className={btnCls} disabled={busy} onClick={() => act(() => systemApi.settings(tenantId, id!, { paused: !link.paused }))}>{link.paused ? "Resume" : "Pause"}</button>}
        {link.connected && isOwner && <button className={btnCls} disabled={busy} title="Issues a new code; the old one stops working" onClick={() => act(async () => { const r = await systemApi.connect(tenantId, link.system); onCode(link.system, r.code); })}>New code</button>}
        {link.connected && isOwner && <button className={btnCls} disabled={busy} onClick={() => {
          if (window.confirm(`Disconnect ${link.label} and delete the ${(link.records ?? 0).toLocaleString()} records it brought in? This cannot be undone. To only stop it for now, use Pause.`))
            act(() => systemApi.disconnect(tenantId, id!), `Disconnected ${link.label}.`);
        }}>Disconnect</button>}
      </div>
    </li>
  );
}

export function SystemLinks({ tenantId, isOwner, onChanged }: { tenantId: string; isOwner: boolean; onChanged?: () => void }) {
  const [links, setLinks] = useState<SystemLink[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [code, setCode] = useState<{ system: string; code: string } | null>(null);
  const load = useCallback(() => systemApi.list(tenantId).then(setLinks).catch((e) => setErr(errText(e))), [tenantId]);
  useEffect(() => { load(); }, [load]);
  const hurry = links.some((l) => l.connected && (l.checking || l.state === "waiting"));
  useEffect(() => { const t = setInterval(load, hurry ? 3000 : 15000); return () => clearInterval(t); }, [load, hurry]);
  const reload = () => { load(); onChanged?.(); };
  const label = links.find((l) => l.system === code?.system)?.label ?? "";
  const downloadUrl = links.find((l) => l.download_url)?.download_url ?? null;
  return (
    <>
      <Notice error={err} notice={notice} />
      {code && (
        <div className="bg-panel border border-field rounded-xl px-5 py-4 mb-3" style={{ borderColor: WARN }}>
          <p className="font-semibold">Connect {label} in two steps</p>
          <ol className="text-sm mt-2 list-decimal pl-5 space-y-2">
            <li>
              On the computer that has {label}, {downloadUrl
                ? <>download and open the agent: <a className="underline font-medium" href={downloadUrl}>Download the agent for Windows</a>. It opens a page in your browser.</>
                : "open the Business Reality Engine agent (ask your support person for it). It opens a page in your browser."}
            </li>
            <li>Paste this code into that page and press Connect. It is shown only once:
              <code className="block mt-2 text-xs break-all bg-panel border border-field rounded-sm p-2 select-all">{code.code}</code>
              <button className={btnCls + " mt-2"} onClick={() => navigator.clipboard?.writeText(code.code)}>Copy the code</button>
            </li>
          </ol>
          <p className="text-sm text-ink-muted mt-2">The agent finds {label}'s data by itself, and this page then shows "Connected and synced". You only do this once per computer; the agent starts with the computer after that.</p>
          <button className={btnCls + " mt-3"} onClick={() => { setCode(null); load(); }}>Done</button>
        </div>
      )}
      <ul className="grid gap-3 md:grid-cols-3">
        {links.map((l) => <Tile key={l.system} link={l} tenantId={tenantId} isOwner={isOwner} reload={reload} onCode={(system, c) => setCode({ system, code: c })} setError={setErr} setNotice={setNotice} />)}
      </ul>
    </>
  );
}
