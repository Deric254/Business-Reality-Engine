import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { activateLicence, ApiError } from "../api/client";
import { Logo } from "../components/Logo";
import { inputCls, primaryCls } from "../components/ui";

export function ActivatePage() {
  const navigate = useNavigate();
  const [f, setF] = useState({ key: "", name: "", email: "", pw: "", pw2: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((p) => ({ ...p, [k]: e.target.value }));
  async function submit(e: FormEvent) {
    e.preventDefault(); setError(null);
    if (f.pw !== f.pw2) return setError("The passwords don't match.");
    setBusy(true);
    try { await activateLicence({ licence_key: f.key.trim(), business_name: f.name.trim(), email: f.email.trim(), password: f.pw }); navigate("/", { replace: true }); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Can't reach the server. Try again."); }
    finally { setBusy(false); }
  }
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-5 py-10">
      <Logo size={60} />
      <form onSubmit={submit} className="w-full max-w-sm space-y-5">
        <div><h1 className="display text-3xl">Activate your licence</h1><p className="text-ink-muted mt-1">One key sets up one business. It can only be used once.</p></div>
        <label className="block text-sm font-medium">Licence key<input required autoComplete="off" spellCheck={false} placeholder="BRE-XXXXX-XXXXX-XXXXX-XXXXX" value={f.key} onChange={set("key")} className={`${inputCls} mt-1.5 !py-2.5 kpi-figure`} /></label>
        <label className="block text-sm font-medium">Business name<input required value={f.name} onChange={set("name")} className={`${inputCls} mt-1.5 !py-2.5`} /></label>
        <label className="block text-sm font-medium">Your email<input type="email" required autoComplete="username" value={f.email} onChange={set("email")} className={`${inputCls} mt-1.5 !py-2.5`} /></label>
        <label className="block text-sm font-medium">Password<input type="password" required minLength={8} autoComplete="new-password" value={f.pw} onChange={set("pw")} className={`${inputCls} mt-1.5 !py-2.5`} /></label>
        <label className="block text-sm font-medium">Repeat password<input type="password" required autoComplete="new-password" value={f.pw2} onChange={set("pw2")} className={`${inputCls} mt-1.5 !py-2.5`} /></label>
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button type="submit" disabled={busy} className={`${primaryCls} w-full !py-2.5 !text-base`}>{busy ? "Activating…" : "Activate"}</button>
        <Link to="/login" className="block text-sm text-info hover:underline">Back to sign in</Link>
      </form>
    </main>
  );
}
