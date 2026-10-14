import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useT } from "../lib/i18n";
import { login, ApiError } from "../api/client";
import { Logo } from "../components/Logo";
import { SlowHint, useSlow } from "../components/ui";

const field = "mt-1.5 w-full bg-panel border border-field rounded-sm px-3 py-2.5 text-base text-ink-text";

export function LoginPage() {
  const navigate = useNavigate();
  const t = useT();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slow = useSlow(busy);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError("Too many attempts. Wait a minute and try again.");
      else if (err instanceof ApiError && err.status === 401) setError("Email or password is incorrect.");
      else setError("Can't reach the server. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-7 px-4 py-10">
      <Logo size={36} />
      <form onSubmit={submit} className="w-full max-w-sm bg-panel border border-border rounded-sm p-7 space-y-5">
        <div>
          <h1 className="text-xl font-bold tracking-tight">Sign in</h1>
          <p className="text-sm text-ink-muted mt-1">See what changed in your business and what to do about it.</p>
        </div>
        <label className="block text-sm font-medium">
          {t("login.email")}
          <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className={field} />
        </label>
        <label className="block text-sm font-medium">
          {t("login.password")}
          <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className={field} />
        </label>
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        {slow && <SlowHint />}
        <button type="submit" disabled={busy} className="w-full bg-info text-white font-semibold rounded-sm px-3 py-2.5 text-base hover:brightness-110 disabled:opacity-60">
          {busy ? "Signing in…" : t("login.submit")}
        </button>
        <Link to="/forgot-password" className="block text-sm text-info hover:underline">{t("login.forgot")}</Link>
      </form>
    </main>
  );
}
