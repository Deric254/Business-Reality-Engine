import { useState } from "react";
import { api } from "../api/client";
import type { QuestionAnswer } from "../api/client";

const EXAMPLES = ["Why did profit fall?", "Who are my top customers?", "What needs my attention?", "What will sales be next week?"];

export function AskBox({ tenantId }: { tenantId: string }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QuestionAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    setBusy(true); setError(null);
    try {
      setResult(await api.askQuestion(tenantId, question));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="ask" className="mb-6">
      <h2 id="ask" className="sr-only">Ask a question</h2>
      <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="flex gap-2">
        <input
          aria-label="Ask a question about your business" maxLength={500} value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Ask about your business, e.g. “Why did profit fall?”"
          className="flex-1 bg-ink border border-border rounded-sm px-3 py-2 text-sm text-ink-text"
        />
        <button type="submit" disabled={busy || !q.trim()} className="bg-info text-ink font-medium rounded-sm px-4 py-2 text-sm disabled:opacity-50">
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>
      {!result && !error && (
        <div className="flex flex-wrap gap-2 mt-2">
          {EXAMPLES.map((e) => (
            <button key={e} type="button" className="text-xs border border-border rounded-sm px-2 py-1 text-ink-muted hover:text-ink-text" onClick={() => { setQ(e); ask(e); }}>{e}</button>
          ))}
        </div>
      )}
      {error && <p role="alert" className="text-sm text-[#f85149] mt-2">{error}</p>}
      {result && (
        <div className="border-l-2 border-info bg-panel px-4 py-3 mt-3" aria-live="polite">
          <p className="text-sm text-ink-text leading-relaxed">{result.answer}</p>
          {!result.answered && result.suggestions && (
            <div className="flex flex-wrap gap-2 mt-2">
              {result.suggestions.map((s) => (
                <button key={s} type="button" className="text-xs border border-border rounded-sm px-2 py-1 text-ink-muted hover:text-ink-text" onClick={() => { setQ(s); ask(s); }}>{s}</button>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
