import { useState } from "react";
import { systemApi, type SystemLink } from "../api/client";
import { errText } from "../lib/errors";
import { TONE_COLOR, describeSystem, type Tone } from "../lib/systemHelp";
import { timeAgo } from "../lib/severity";
import { btnCls, inputCls, primaryCls } from "./ui";

/** One connected system: its name, how it is doing in plain words, and everything else tucked under "Manage". */
function Row({ link, tenantId, isOwner, reload, onOpen, setError, setNotice }: {
  link: SystemLink; tenantId: string; isOwner: boolean; reload: () => void; onOpen: (system: string) => void;
  setError: (s: string | null) => void; setNotice: (s: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [path, setPath] = useState("");
  const id = link.connector_id!;
  const plain = describeSystem(link);
  const tone: Tone = link.paused ? "wait" : plain.tone;
  const st = link.status ?? {};
  const counts = Object.entries(link.last_pass?.rows ?? {}).map(([e, n]) => `${n.toLocaleString("en-US")} ${e}${n === 1 ? "" : "s"}`).join(", ");
  async function act(fn: () => Promise<unknown>, done?: string) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); if (done) setNotice(done); reload(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }
  const needsHelp = link.state !== "connected" && !link.paused;
  return (
    <li className="px-4 sm:px-5 py-4 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="font-semibold">{link.label}</div>
          <div className="text-sm text-ink-muted">{link.state === "connected" && counts ? `${counts}${link.last_synced_at ? `, updated ${timeAgo(link.last_synced_at)}` : ""}` : "Business software on your computer"}</div>
        </div>
        <span className="text-sm flex items-center gap-2" role="status"><span className="w-2 h-2 rounded-full" style={{ background: TONE_COLOR[tone] }} aria-hidden />{link.paused ? "Paused" : link.checking ? "Checking…" : plain.title}</span>
      </div>
      {needsHelp && <div className="text-sm text-ink-muted" role="alert">{plain.todo}{link.detail ? <span className="block text-xs mt-1">Details: {link.detail}</span> : null}</div>}
      {link.state === "needs_choice" && isOwner && (st.scopes?.length ?? 0) > 0 && (
        <label className="text-sm flex items-center gap-2">Business to read
          <select className={inputCls + " max-w-xs"} defaultValue="" onChange={(e) => e.target.value && act(() => systemApi.settings(tenantId, id, { scope_id: e.target.value }))}>
            <option value="" disabled>Choose…</option>
            {st.scopes!.map((s) => <option key={s.id} value={s.id}>{s.name || s.id}</option>)}
          </select>
        </label>
      )}
      {link.state === "needs_confirmation" && isOwner && <button className={primaryCls} disabled={busy} onClick={() => act(() => systemApi.confirm(tenantId, id), "Confirmed. The data is being brought up to date.")}>Confirm the change</button>}
      {needsHelp && isOwner && link.state === "waiting" && <button className={btnCls} onClick={() => onOpen(link.system)}>Show me the steps</button>}
      {isOwner && (
        <details className="text-sm">
          <summary className="cursor-pointer text-ink-muted py-2 lg:py-0">Manage</summary>
          <div className="mt-2 space-y-2">
            {link.notice && <div className="text-ink-muted">{link.notice}</div>}
            {st.path && <div className="text-xs text-ink-muted break-all">Reading from {st.path} ({st.path_source}){st.scope_name ? `, business: ${st.scope_name}` : ""}</div>}
            {link.last_seen_at && <div className="text-xs text-ink-muted">The helper app last reported {timeAgo(link.last_seen_at)}.</div>}
            {editing && (
              <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); act(() => systemApi.settings(tenantId, id, { path: path.trim() }), path.trim() ? "Location saved. Checking it now." : "Looking for it automatically again."); setEditing(false); }}>
                <input className={inputCls + " flex-1 min-w-[240px]"} placeholder="Full path of the data file (leave empty to find it by itself)" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Path of the data file" />
                <button className={primaryCls} disabled={busy}>Save</button><button type="button" className={btnCls} onClick={() => setEditing(false)}>Cancel</button>
              </form>
            )}
            <div className="flex flex-wrap gap-2">
              <button className={btnCls} disabled={busy || link.checking} onClick={() => act(() => systemApi.check(tenantId, id))}>{link.checking ? "Checking…" : "Check again"}</button>
              <button className={btnCls} onClick={() => { setEditing(!editing); setPath(st.path_source === "set by hand" ? st.path ?? "" : ""); }}>Set location</button>
              <button className={btnCls} disabled={busy} title="Compares everything with the system and repairs any difference" onClick={() => act(() => systemApi.resync(tenantId, id), "Re-checking everything now.")}>Re-check everything</button>
              <button className={btnCls} disabled={busy} onClick={() => act(() => systemApi.settings(tenantId, id, { paused: !link.paused }))}>{link.paused ? "Resume" : "Pause"}</button>
              <button className={btnCls} disabled={busy} title="The old code stops working" onClick={() => onOpen(link.system)}>Get a new code</button>
              <button className={btnCls} disabled={busy} onClick={() => {
                if (window.confirm(`Disconnect ${link.label} and delete the ${(link.records ?? 0).toLocaleString("en-US")} records it brought in? This cannot be undone. To only stop it for now, use Pause.`))
                  act(() => systemApi.disconnect(tenantId, id), `Disconnected ${link.label}.`);
              }}>Disconnect</button>
            </div>
          </div>
        </details>
      )}
    </li>
  );
}

/** The systems that are connected. (Connecting one happens in the guided flow, not here.) */
export function SystemRows({ links, tenantId, isOwner, reload, onOpen, setError, setNotice }: {
  links: SystemLink[]; tenantId: string; isOwner: boolean; reload: () => void; onOpen: (system: string) => void;
  setError: (s: string | null) => void; setNotice: (s: string | null) => void;
}) {
  const connected = links.filter((l) => l.connected);
  if (connected.length === 0) return null;
  return <>{connected.map((l) => <Row key={l.system} link={l} tenantId={tenantId} isOwner={isOwner} reload={reload} onOpen={onOpen} setError={setError} setNotice={setNotice} />)}</>;
}

