import { useState, type FormEvent } from "react";
import { changePassword, ApiError } from "../api/client";

const input = "mt-1 w-full bg-panel border border-field rounded-sm px-3 py-2 text-sm text-ink-text";

export function PasswordForm({ onDone }: { onDone?: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null); setDone(false);
    if (next !== again) return setError("The new passwords don't match.");
    if (next.length < 8) return setError("Use at least 8 characters.");
    if (new TextEncoder().encode(next).length > 72) return setError("Use at most 72 bytes (about 72 characters).");
    setBusy(true);
    try {
      await changePassword(current, next);
      setCurrent(""); setNext(""); setAgain(""); setDone(true);
      onDone?.();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError("The current password is incorrect.");
      else if (err instanceof ApiError && err.status === 429) setError("Too many attempts. Try again later.");
      else setError(err instanceof ApiError ? err.message : "Can't reach the server. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 max-w-sm">
      <label className="block text-sm text-ink-muted">Current password
        <input type="password" required autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} className={input} />
      </label>
      <label className="block text-sm text-ink-muted">New password
        <input type="password" required autoComplete="new-password" minLength={8} value={next} onChange={(e) => setNext(e.target.value)} className={input} />
      </label>
      <label className="block text-sm text-ink-muted">Repeat new password
        <input type="password" required autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} className={input} />
      </label>
      {error && <p role="alert" className="text-sm text-critical">{error}</p>}
      {done && <p role="status" className="text-sm text-opportunity">Password changed. Other devices were signed out.</p>}
      <button type="submit" disabled={busy} className="bg-info text-white font-semibold hover:brightness-110 rounded-sm px-3 py-2 text-sm disabled:opacity-60">
        {busy ? "Saving…" : "Change password"}
      </button>
    </form>
  );
}
