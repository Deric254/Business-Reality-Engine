import { PasswordForm } from "../components/PasswordForm";
import { logout } from "../api/client";

/** Shown instead of the app while the account is on an owner-issued temporary password. */
export function ChangePasswordPage() {
  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-sm bg-panel border border-border rounded-sm p-6 space-y-4">
        <h1 className="text-lg font-semibold tracking-tight">Choose your own password</h1>
        <p className="text-sm text-ink-muted">You signed in with a temporary password. Set a new one to continue; enter the temporary password as the current one.</p>
        <PasswordForm />
        <button onClick={logout} className="text-xs text-ink-muted hover:text-ink-text">Sign out</button>
      </div>
    </main>
  );
}
