import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { QuestionAnswer } from "../api/client";
import { primaryCls } from "./ui";

const EXAMPLES = ["Why did profit fall?", "Who are my top customers?", "What needs my attention?", "What will sales be next week?"];
const chip = "text-sm border border-field rounded-full px-3 py-1 text-ink-text bg-panel hover:bg-panel-raised transition-colors";

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
      {error && <p role="alert" className="text-sm text-critical mt-3">{error}</p>}
      {result && (
        <div className="border-l-4 border-info bg-info-soft/60 rounded-sm px-4 py-3 mt-4" aria-live="polite">
          <p className="text-base text-ink-text leading-relaxed">{result.answer}</p>
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
