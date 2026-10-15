import { useState, type FormEvent } from "react";
import { Logo } from "../components/Logo";
import { Link } from "react-router-dom";
import { forgotPassword, setupApi, ApiError } from "../api/client";
import { useT } from "../lib/i18n";

const inputCls = "mt-1 w-full bg-panel border border-field rounded-sm px-3 py-2 text-ink-text";
const buttonCls = "w-full bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-2 disabled:opacity-60";

export function ForgotPasswordPage() {
  const t = useT();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [emailEnabled, setEmailEnabled] = useState(true);
  const [setupKey, setSetupKey] = useState("");
  const [reset, setReset] = useState<{ link: string; expires_in_minutes: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const res = await forgotPassword(email.trim());
      setEmailEnabled(res.email_enabled);
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429 ? "Too many attempts. Wait a minute and try again." : "Can't reach the server. Try again.");
    } finally { setBusy(false); }
  }

  async function getLink(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      setReset(await setupApi.resetLink(setupKey.trim(), email.trim()));
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) setError("That setup key is not correct. It is the ADMIN_API_KEY value in your hosting settings.");
      else if (err instanceof ApiError && err.status === 503) setError("No setup key is configured on the server. Set ADMIN_API_KEY in your hosting settings and redeploy.");
      else if (err instanceof ApiError && err.status === 404) setError("No account uses that email.");
      else setError("Can't reach the server. Try again.");
    } finally { setBusy(false); }
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 py-10">
      <Logo size={56} />
      <form onSubmit={sent ? getLink : submit} className="w-full max-w-sm bg-panel border border-border rounded-sm p-7 space-y-4">
        <h1 className="display text-2xl">{t("forgot.title")}</h1>
        {!sent && (
          <>
            <p className="text-sm text-ink-muted">{t("forgot.help")}</p>
            <label className="block text-sm"><span className="text-ink-muted">{t("login.email")}</span>
              <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} />
            </label>
            {error && <p role="alert" className="text-sm text-critical">{error}</p>}
            <button type="submit" disabled={busy} className={buttonCls}>{t("forgot.send")}</button>
          </>
        )}
        {sent && emailEnabled && <p role="status" className="text-sm text-opportunity">{t("forgot.sent")}</p>}
        {sent && !emailEnabled && !reset && (
          <>
            <p role="status" className="text-sm">Email is not set up on this server, so nothing was sent.</p>
            <p className="text-sm text-ink-muted">If you run this installation, enter the setup key to get a reset link for {email.trim()} right here.</p>
            <label className="block text-sm"><span className="text-ink-muted">Setup key</span>
              <input type="password" required autoComplete="off" value={setupKey} onChange={(e) => setSetupKey(e.target.value)} className={inputCls} />
              <span className="block text-xs text-ink-faint mt-1">The ADMIN_API_KEY value from your hosting environment settings.</span>
            </label>
            {error && <p role="alert" className="text-sm text-critical">{error}</p>}
            <button type="submit" disabled={busy} className={buttonCls}>Get reset link</button>
          </>
        )}
        {reset && (
          <>
            <p role="status" className="text-sm text-opportunity">Your reset link is ready. It works once and expires in {reset.expires_in_minutes} minutes.</p>
            <a href={reset.link} className="block text-sm text-info hover:underline break-all">{reset.link}</a>
          </>
        )}
        <Link to="/recover" className="block text-sm text-info hover:underline">Use your security questions instead</Link>
        <Link to="/login" className="block text-xs text-ink-muted hover:text-ink-text">{t("back.signin")}</Link>
      </form>
    </main>
  );
}
