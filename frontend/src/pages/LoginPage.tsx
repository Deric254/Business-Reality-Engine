import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useT } from "../lib/i18n";
import { login, setupApi, ApiError, type SetupStatus } from "../api/client";
import { Logo } from "../components/Logo";
import { SlowHint, useSlow, inputCls, primaryCls } from "../components/ui";

function Frame({ children }: { children: ReactNode }) {
  return (
    <main className="min-h-screen grid lg:grid-cols-[1.1fr_1fr]">
      <section className="hidden lg:flex flex-col justify-between bg-info text-white p-12">
        <Logo size={110} light stacked />
        <div className="max-w-md space-y-6">
          <h2 className="display text-5xl text-white">Know what is really happening in your business.</h2>
          <ul className="space-y-3 text-lg text-white/85">
            <li>It reads the systems you already use.</li>
            <li>It tells you when something moves, and why.</li>
            <li>It remembers what you decided and what happened next.</li>
          </ul>
        </div>
        <p className="text-sm text-white/70">Your data stays yours. Every business is kept completely separate.</p>
      </section>
      <section className="flex flex-col items-center justify-center gap-6 px-5 py-10">
        <div className="lg:hidden"><Logo size={84} stacked /></div>
        {children}
      </section>
    </main>
  );
}

const card = "w-full max-w-sm space-y-5";

function SignIn({ onFirstRun }: { onFirstRun?: () => void }) {
  const navigate = useNavigate();
  const t = useT();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slow = useSlow(busy);

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { await login(email.trim(), password); navigate("/", { replace: true }); }
    catch (err) {
      if (err instanceof ApiError && err.status === 429) setError("Too many attempts. Wait a minute and try again.");
      else if (err instanceof ApiError && err.status === 401) setError("Email or password is incorrect.");
      else setError("Can't reach the server. Check your connection and try again.");
    } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className={card}>
      <div><h1 className="display text-3xl">Sign in</h1><p className="text-ink-muted mt-1">Pick up where you left off.</p></div>
      <label className="block text-sm font-medium">{t("login.email")}<input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className={`${inputCls} mt-1.5 !py-2.5 !text-base`} /></label>
      <label className="block text-sm font-medium">{t("login.password")}<input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className={`${inputCls} mt-1.5 !py-2.5 !text-base`} /></label>
      {error && <p role="alert" className="text-sm text-critical">{error}</p>}
      {slow && <SlowHint />}
      <button type="submit" disabled={busy} className={`${primaryCls} w-full !py-2.5 !text-base`}>{busy ? "Signing in…" : t("login.submit")}</button>
      <div className="flex justify-between text-sm"><Link to="/forgot-password" className="text-info hover:underline">{t("login.forgot")}</Link>{onFirstRun && <button type="button" onClick={onFirstRun} className="text-info hover:underline">Set up a new business</button>}</div>
    </form>
  );
}

function FirstRun({ status, onBack }: { status: SetupStatus; onBack?: () => void }) {
  const navigate = useNavigate();
  const [f, setF] = useState({ key: "", name: "", email: "", pw: "", pw2: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((p) => ({ ...p, [k]: e.target.value }));
  async function submit(e: FormEvent) {
    e.preventDefault(); setError(null);
    if (f.pw !== f.pw2) return setError("The passwords don't match.");
    if (f.pw.length < 8) return setError("Use at least 8 characters for the password.");
    setBusy(true);
    try {
      await setupApi.createBusiness(f.key.trim(), { name: f.name.trim(), owner_email: f.email.trim(), owner_password: f.pw });
      await login(f.email.trim(), f.pw);
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) setError("That setup key is not correct. It is the ADMIN_API_KEY value in your hosting settings.");
      else if (err instanceof ApiError && err.status === 503) setError("No setup key is configured on the server. Set ADMIN_API_KEY in your hosting settings and redeploy.");
      else setError(err instanceof ApiError ? err.message : "Can't reach the server. Try again.");
    } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className={card}>
      <div><h1 className="display text-3xl">{status.has_business ? "Add a business" : "Create your business"}</h1>
        <p className="text-ink-muted mt-1">{status.has_business ? "You need the setup key to add another business." : "This is a new installation. Create the first business and its owner login."}</p></div>
      <label className="block text-sm font-medium">Setup key<input type="password" required autoComplete="off" value={f.key} onChange={set("key")} className={`${inputCls} mt-1.5 !py-2.5`} />
        <span className="block text-xs text-ink-faint font-normal mt-1">The ADMIN_API_KEY value from your hosting environment settings.</span></label>
      <label className="block text-sm font-medium">Business name<input required value={f.name} onChange={set("name")} className={`${inputCls} mt-1.5 !py-2.5`} /></label>
      <label className="block text-sm font-medium">Your email<input type="email" required autoComplete="username" value={f.email} onChange={set("email")} className={`${inputCls} mt-1.5 !py-2.5`} /></label>
      <label className="block text-sm font-medium">Password<input type="password" required minLength={8} autoComplete="new-password" value={f.pw} onChange={set("pw")} className={`${inputCls} mt-1.5 !py-2.5`} /></label>
      <label className="block text-sm font-medium">Repeat password<input type="password" required autoComplete="new-password" value={f.pw2} onChange={set("pw2")} className={`${inputCls} mt-1.5 !py-2.5`} /></label>
      {error && <p role="alert" className="text-sm text-critical">{error}</p>}
      <button type="submit" disabled={busy} className={`${primaryCls} w-full !py-2.5 !text-base`}>{busy ? "Creating…" : "Create business"}</button>
      {onBack && <button type="button" onClick={onBack} className="text-sm text-info hover:underline">Back to sign in</button>}
    </form>
  );
}

export function LoginPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [creating, setCreating] = useState(false);
  useEffect(() => { setupApi.status().then(setStatus).catch(() => setStatus(null)); }, []);
  const first = status != null && !status.has_business;
  return <Frame>{first || creating ? <FirstRun status={status ?? { has_business: true, setup_key_configured: true }} onBack={first ? undefined : () => setCreating(false)} /> : <SignIn onFirstRun={status ? () => setCreating(true) : undefined} />}</Frame>;
}
