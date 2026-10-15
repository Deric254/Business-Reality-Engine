import { useState, type FormEvent } from "react";
import { Logo } from "../components/Logo";
import { Link } from "react-router-dom";
import { recoveryQuestions, recoverWithAnswers, ApiError } from "../api/client";
import { useT } from "../lib/i18n";

const inputCls = "mt-1 w-full bg-panel border border-field rounded-sm px-3 py-2 text-ink-text";
const buttonCls = "w-full bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-2 disabled:opacity-60";

/** Get back into an account by answering its security questions. Needs no email. */
export function RecoverPage() {
  const t = useT();
  const [email, setEmail] = useState("");
  const [questions, setQuestions] = useState<string[] | null>(null);
  const [answers, setAnswers] = useState<string[]>([]);
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function showQuestions(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await recoveryQuestions(email.trim());
      setQuestions(r.questions); setAnswers(Array(r.questions.length).fill(""));
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429 ? "Too many attempts. Wait a minute and try again." : "Can't reach the server. Try again.");
    } finally { setBusy(false); }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw !== again) return setError("The passwords don't match.");
    if (pw.length < 8) return setError("Use at least 8 characters.");
    if (new TextEncoder().encode(pw).length > 72) return setError("Use at most 72 bytes (about 72 characters).");
    setBusy(true);
    try {
      await recoverWithAnswers(email.trim(), answers, pw);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError("Too many wrong tries. Wait 15 minutes, or use the email link instead.");
      else setError(err instanceof ApiError ? err.message : "Can't reach the server. Try again.");
    } finally { setBusy(false); }
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 py-10">
      <Logo size={56} />
      <form onSubmit={questions ? submit : showQuestions} className="w-full max-w-sm bg-panel border border-border rounded-sm p-7 space-y-4">
        <h1 className="display text-2xl">Answer your security questions</h1>
        {done ? (
          <>
            <p role="status" className="text-sm text-opportunity">Your password is changed. Every device was signed out.</p>
            <Link to="/login" className="block text-sm text-info hover:underline">Sign in</Link>
          </>
        ) : !questions ? (
          <>
            <p className="text-sm text-ink-muted">Enter your account email and we'll show the questions you chose.</p>
            <label className="block text-sm"><span className="text-ink-muted">{t("login.email")}</span>
              <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} /></label>
            {error && <p role="alert" className="text-sm text-critical">{error}</p>}
            <button type="submit" disabled={busy} className={buttonCls}>Show my questions</button>
          </>
        ) : (
          <>
            <p className="text-sm text-ink-muted">Answer all {questions.length}. Capital letters, spaces and accents don't matter.</p>
            {questions.map((q, i) => (
              <label key={q} className="block text-sm"><span className="text-ink-muted">{q}</span>
                <input required autoComplete="off" value={answers[i] ?? ""} onChange={(e) => setAnswers((a) => a.map((x, j) => (j === i ? e.target.value : x)))} className={inputCls} /></label>
            ))}
            <label className="block text-sm"><span className="text-ink-muted">{t("reset.new")}</span>
              <input type="password" required minLength={8} autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} className={inputCls} /></label>
            <label className="block text-sm"><span className="text-ink-muted">{t("reset.again")}</span>
              <input type="password" required autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} className={inputCls} /></label>
            {error && <p role="alert" className="text-sm text-critical">{error}</p>}
            <button type="submit" disabled={busy} className={buttonCls}>{t("reset.submit")}</button>
          </>
        )}
        {!done && <Link to="/forgot-password" className="block text-sm text-info hover:underline">Email me a reset link instead</Link>}
        <Link to="/login" className="block text-xs text-ink-muted hover:text-ink-text">{t("back.signin")}</Link>
      </form>
    </main>
  );
}
