import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login, ApiError } from "../api/client";

export function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    <main className="min-h-screen flex items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-panel border border-border rounded-sm p-6 space-y-4">
        <h1 className="text-lg font-semibold tracking-tight">Sign in to Business Reality</h1>
        <label className="block text-sm">
          <span className="text-ink-muted">Email</span>
          <input
            type="email" required autoComplete="username" value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full bg-ink border border-border rounded-sm px-3 py-2 text-ink-text"
          />
        </label>
        <label className="block text-sm">
          <span className="text-ink-muted">Password</span>
          <input
            type="password" required autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full bg-ink border border-border rounded-sm px-3 py-2 text-ink-text"
          />
        </label>
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button
          type="submit" disabled={busy}
          className="w-full bg-info text-ink font-medium rounded-sm px-3 py-2 disabled:opacity-60"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
