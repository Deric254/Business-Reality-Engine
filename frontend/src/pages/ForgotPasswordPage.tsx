import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { forgotPassword, ApiError } from "../api/client";
import { useT } from "../lib/i18n";

export function ForgotPasswordPage() {
  const t = useT();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429 ? "Too many attempts. Wait a minute and try again." : "Can't reach the server. Try again.");
    } finally { setBusy(false); }
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-panel border border-border rounded-sm p-6 space-y-4">
        <h1 className="text-lg font-semibold tracking-tight">{t("forgot.title")}</h1>
        {sent ? (
          <p role="status" className="text-sm text-opportunity">{t("forgot.sent")}</p>
        ) : (
          <>
            <p className="text-sm text-ink-muted">{t("forgot.help")}</p>
            <label className="block text-sm"><span className="text-ink-muted">{t("login.email")}</span>
              <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full bg-ink border border-border rounded-sm px-3 py-2 text-ink-text" />
            </label>
            {error && <p role="alert" className="text-sm text-critical">{error}</p>}
            <button type="submit" disabled={busy} className="w-full bg-info text-ink font-medium rounded-sm px-3 py-2 disabled:opacity-60">{t("forgot.send")}</button>
          </>
        )}
        <Link to="/login" className="block text-xs text-ink-muted hover:text-ink-text">{t("back.signin")}</Link>
      </form>
    </main>
  );
}
