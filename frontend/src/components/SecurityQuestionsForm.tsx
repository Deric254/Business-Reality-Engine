import { useEffect, useState, type FormEvent } from "react";
import { ApiError, securityQuestionsApi } from "../api/client";
import { cache } from "../api/cache";

const input = "mt-1 w-full bg-panel border border-field rounded-sm px-3 py-2 text-sm text-ink-text";

export function SecurityQuestionsForm() {
  const [options, setOptions] = useState<string[]>([]);
  const [count, setCount] = useState(3);
  const [saved, setSaved] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [answers, setAnswers] = useState<string[]>([]);
  const [current, setCurrent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    securityQuestionsApi.get().then((r) => {
      setOptions(r.options); setCount(r.count); setSaved(r.questions);
      setPicked(r.options.slice(0, r.count)); setAnswers(Array(r.count).fill(""));
      setEditing(r.questions.length === 0);
    }).catch(() => setError("Can't load your security questions. Reload and try again."));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null); setBusy(true);
    try {
      const r = await securityQuestionsApi.save(current, picked.map((question, i) => ({ question, answer: answers[i] })));
      setSaved(r.questions); cache.recoverySet = true;
      setEditing(false); setCurrent(""); setAnswers(Array(count).fill(""));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError("The current password is incorrect.");
      else if (err instanceof ApiError && err.status === 429) setError("Too many attempts. Try again later.");
      else setError(err instanceof ApiError ? err.message : "Can't reach the server. Try again.");
    } finally { setBusy(false); }
  }

  if (!editing) {
    return (
      <div className="space-y-3 max-w-sm">
        <p role="status" className="text-sm text-opportunity">Set. If you forget your password you can answer these to get back in:</p>
        <ul className="text-sm list-disc pl-5">{saved.map((q) => <li key={q}>{q}</li>)}</ul>
        <p className="text-xs text-ink-faint">Your answers are stored scrambled and cannot be shown again.</p>
        <button type="button" onClick={() => setEditing(true)} className="border border-field rounded-sm px-3 py-1.5 text-sm hover:bg-panel-raised">Change questions</button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-3 max-w-sm">
      <p className="text-sm text-ink-muted">Pick {count} questions only you can answer. To reset your password you must answer all {count}. Capital letters, spaces and accents don't matter.</p>
      {picked.map((q, i) => (
        <div key={i} className="space-y-1">
          <select aria-label={`Question ${i + 1}`} className={input} value={q} onChange={(e) => setPicked((p) => p.map((x, j) => (j === i ? e.target.value : x)))}>
            {options.map((o) => <option key={o} value={o} disabled={picked.includes(o) && o !== q}>{o}</option>)}
          </select>
          <input aria-label={`Answer ${i + 1}`} required minLength={3} autoComplete="off" value={answers[i] ?? ""} onChange={(e) => setAnswers((a) => a.map((x, j) => (j === i ? e.target.value : x)))} className={input} placeholder="Your answer" />
        </div>
      ))}
      <label className="block text-sm text-ink-muted">Current password
        <input type="password" required autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} className={input} />
        <span className="block text-xs text-ink-faint mt-1">Needed so nobody using your open screen can change how your account is recovered.</span>
      </label>
      {error && <p role="alert" className="text-sm text-critical">{error}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className="bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-2 text-sm disabled:opacity-60">{busy ? "Saving…" : "Save questions"}</button>
        {saved.length > 0 && <button type="button" onClick={() => { setEditing(false); setError(null); }} className="border border-field rounded-sm px-3 py-2 text-sm hover:bg-panel-raised">Cancel</button>}
      </div>
    </form>
  );
}
