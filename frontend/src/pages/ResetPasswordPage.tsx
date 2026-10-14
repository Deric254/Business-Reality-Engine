import { useState, type FormEvent } from "react";
import { Logo } from "../components/Logo";
import { Link, useSearchParams } from "react-router-dom";
import { resetPassword, ApiError } from "../api/client";
import { useT } from "../lib/i18n";

/** Used for both "reset-password" and "accept-invite": the server treats both tokens the same way. */
export function ResetPasswordPage({ invite = false }: { invite?: boolean }) {
  const t = useT();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw !== again) return setError("The passwords don't match.");
    if (pw.length < 8) return setError("Use at least 8 characters.");
    if (new TextEncoder().encode(pw).length > 72) return setError("Use at most 72 bytes (about 72 characters).");
    setBusy(true);
    try {
      await resetPassword(token, pw);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError("Too many attempts. Wait a minute and try again.");
      else setError(err instanceof ApiError ? err.message : "Can't reach the server. Try again.");
    } finally { setBusy(false); }
  }

  const input = "mt-1 w-full bg-panel border border-field rounded-sm px-3 py-2 text-ink-text";
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 py-10">
      <Logo size={32} />
      <form onSubmit={submit} className="w-full max-w-sm bg-panel border border-border rounded-sm p-7 space-y-4">
        <h1 className="text-lg font-semibold tracking-tight">{invite ? t("invite.title") : t("reset.title")}</h1>
        {!token ? (
          <p role="alert" className="text-sm text-critical">This link is incomplete. Open the link from your email again.</p>
        ) : done ? (
          <p role="status" className="text-sm text-opportunity">{t("reset.done")}</p>
        ) : (
          <>
            <label className="block text-sm"><span className="text-ink-muted">{t("reset.new")}</span>
              <input type="password" required minLength={8} autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} className={input} /></label>
            <label className="block text-sm"><span className="text-ink-muted">{t("reset.again")}</span>
              <input type="password" required autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} className={input} /></label>
            {error && <p role="alert" className="text-sm text-critical">{error}</p>}
            <button type="submit" disabled={busy} className="w-full bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-2 disabled:opacity-60">{t("reset.submit")}</button>
          </>
        )}
        <Link to="/login" className="block text-xs text-ink-muted hover:text-ink-text">{t("back.signin")}</Link>
      </form>
    </main>
  );
}
