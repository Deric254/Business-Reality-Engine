import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { QuestionAnswer } from "../api/client";
import { primaryCls } from "./ui";

const EXAMPLES = ["Why did profit fall?", "Who are my top customers?", "What needs my attention?", "What will sales be next week?"];
const chip = "text-sm border border-field rounded-full px-3 py-1 text-ink-text bg-panel hover:bg-panel-raised transition-colors";

type Block = { kind: "p"; text: string } | { kind: "list"; items: string[] };

/** Turns an answer into short readable blocks: paragraphs and lists, never one wall of text. Plain text only. */
function toBlocks(raw: string): Block[] {
  let text = raw.replace(/\r/g, "").trim();
  // A run-on numbered list ("... 1. Where ... 2. What ... 3. Which") becomes one item per line.
  if ((text.match(/(?:^|\s)\d{1,2}[.)]\s+\S/g) || []).length >= 3) {
    text = text.replace(/\s+(?=\d{1,2}[.)]\s+\S)/g, "\n");
    // the prose that follows the last item starts a new paragraph, not part of that item
    text = text.replace(/^(\d{1,2}[.)]\s+[^\n]*?[a-z\]])\s+((?:These|This|The|Using|You|It|For|If|Your|That)\s[^\n]*)$/gm, (m, a, b, off, all) => (all.indexOf("\n", off) === -1 || !/\n\d{1,2}[.)]/.test(all.slice(off + m.length)) ? `${a}\n\n${b}` : m));
  }
  // One long paragraph is cut into pairs of sentences.
  if (!text.includes("\n") && text.length > 240) {
    const sentences = text.match(/[^.!?]+(?:[.!?]+(?:\s*\[\d+\])*|$)\s*/g) || [text];
    const out: string[] = [];
    for (let i = 0; i < sentences.length; i += 2) out.push(sentences.slice(i, i + 2).join("").trim());
    text = out.join("\n\n");
  }
  const blocks: Block[] = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    const m = t.match(/^(?:[-*\u2022]|\d{1,2}[.)])\s+(.*)$/);
    const last = blocks[blocks.length - 1];
    if (m) { if (last && last.kind === "list") last.items.push(m[1]); else blocks.push({ kind: "list", items: [m[1]] }); }
    else blocks.push({ kind: "p", text: t });
  }
  return blocks;
}

/** [1] markers become small links to the page they came from. */
function withCitations(text: string, sources?: { title: string; url: string }[]) {
  return text.split(/(\[\d+\])/g).map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    const src = m && sources ? sources[Number(m[1]) - 1] : undefined;
    return src
      ? <a key={i} href={src.url} target="_blank" rel="noopener noreferrer" title={src.title} className="text-info hover:underline text-sm align-super">{part}</a>
      : <span key={i}>{part}</span>;
  });
}

function Answer({ text, sources }: { text: string; sources?: { title: string; url: string }[] }) {
  return (
    <div className="space-y-3 text-base text-ink-text leading-relaxed break-words">
      {toBlocks(text).map((b, i) => b.kind === "p"
        ? <p key={i}>{withCitations(b.text, sources)}</p>
        : <ul key={i} className="space-y-2 pl-5 list-disc marker:text-ink-faint">{b.items.map((it, j) => <li key={j} className="pl-1">{withCitations(it, sources)}</li>)}</ul>)}
    </div>
  );
}

const STEPS: [number, string][] = [
  [0, "Working on your question"],
  [3, "Looking for the answer"],
  [7, "Writing the answer"],
  [20, "Still working. Some answers take up to half a minute"],
];

/** Shown while a question is being answered: moving dots, what is happening, and seconds so far, so it never looks frozen. */
function Working() {
  const [sec, setSec] = useState(0);
  useEffect(() => { const t = setInterval(() => setSec((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const step = [...STEPS].reverse().find(([from]) => sec >= from)![1];
  return (
    <div role="status" aria-live="polite" className="mt-4 rounded-sm border border-border bg-panel-raised px-4 py-3">
      <div className="flex items-center gap-3 text-sm text-ink-text">
        <span className="flex gap-1" aria-hidden>
          {[0, 1, 2].map((i) => <span key={i} className="h-2 w-2 rounded-full bg-info animate-bounce" style={{ animationDelay: `${i * 150}ms` }} />)}
        </span>
        <span>{step}…</span>
        <span className="ml-auto text-ink-muted tabular-nums">{sec}s</span>
      </div>
      <div className="mt-2 h-1 rounded-full bg-border overflow-hidden"><div className="h-full w-1/3 bg-info rounded-full animate-pulse" style={{ marginLeft: `${Math.min(66, sec * 3)}%`, transition: "margin 1s linear" }} /></div>
    </div>
  );
}

export function AskBox({ tenantId, days }: { tenantId: string; days: number }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QuestionAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    setBusy(true); setError(null);
    try { setResult(await api.askQuestion(tenantId, question, days)); }
    catch (e) { setError(e instanceof Error ? e.message : "Something went wrong."); }
    finally { setBusy(false); }
  }

  return (
    <section aria-labelledby="ask" className="bg-panel border border-border rounded-xl p-5 sm:p-6">
      <h2 id="ask" className="text-lg font-semibold tracking-tight">Ask your business a question</h2>
      <p className="text-sm text-ink-muted mt-0.5 mb-3">Answers use only your own numbers, for the last {days} days unless you say otherwise.</p>
      <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="flex gap-2">
        <input aria-label="Ask a question about your business" maxLength={500} value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="For example: Why did profit fall?" className="flex-1 min-w-0 bg-panel border border-field rounded-sm px-3 py-2.5 text-sm text-ink-text" />
        <button type="submit" disabled={busy || !q.trim()} className={`${primaryCls} px-5`}>{busy ? "Asking…" : "Ask"}</button>
      </form>
      {!result && !error && (
        <div className="flex flex-wrap gap-2 mt-3">
          {EXAMPLES.map((e) => <button key={e} type="button" className={chip} onClick={() => { setQ(e); ask(e); }}>{e}</button>)}
        </div>
      )}
      {busy && <Working />}
      {error && <p role="alert" className="text-sm text-critical mt-3">{error}</p>}
      {result && !busy && (
        <div className="border-l-4 border-info bg-info-soft/60 rounded-sm px-4 py-3 mt-4" aria-live="polite">
          {result.answer_source === "web" && (
            <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted mb-1">From the web, not from your numbers</p>
          )}
          <Answer text={result.answer} sources={result.sources} />
          {result.answer_source === "web" && result.search_query && (
            <p className="text-xs text-ink-muted mt-2">Searched for: <span className="text-ink-text">{result.search_query}</span></p>
          )}
          {result.answer_source === "web" && result.needs_area && (
            <p className="text-sm text-ink-muted mt-2">
              For named businesses near you, tell us where you trade: <Link to="/settings" className="text-info hover:underline">set your business area in Settings</Link>.
            </p>
          )}
          {result.sources && result.sources.length > 0 && (
            <ol className="text-sm text-ink-muted mt-3 border-t border-border pt-3 space-y-1">
              {result.sources.map((s, i) => (
                <li key={s.url} className="break-words">[{i + 1}]{" "}
                  <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-info hover:underline">{s.title}</a>
                </li>
              ))}
            </ol>
          )}
          {!result.answered && result.ai_connected === false && (
            <p className="text-sm text-ink-muted mt-3 border-t border-border pt-3">
              The AI assistant is not connected, so only the fixed questions below work. To ask about your sales, expenses and profit in your own words,{" "}
              <Link to="/settings#ai" className="text-info hover:underline">connect it in Settings</Link>.
            </p>
          )}
          {!result.answered && result.suggestions && (
            <div className="flex flex-wrap gap-2 mt-3">
              {result.suggestions.map((s) => <button key={s} type="button" className={chip} onClick={() => { setQ(s); ask(s); }}>{s}</button>)}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
