import { useSyncExternalStore, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { AlertDetailPage } from "./pages/AlertDetailPage";
import { SettingsPage } from "./pages/SettingsPage";
import { LoginPage } from "./pages/LoginPage";
import { BusinessPage } from "./pages/BusinessPage";
import { DataPage } from "./pages/DataPage";
import { AccuracyPage } from "./pages/AccuracyPage";
import { SetupWizardPage } from "./pages/SetupWizardPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { getClaims, getTokens, isSignedIn, onSessionChange } from "./api/session";

function useSignedIn() {
  return useSyncExternalStore(onSessionChange, isSignedIn);
}

function RequireAuth({ children }: { children: ReactNode }) {
  // Subscribe to the access token itself (not just "is there one"): a password change swaps the token,
  // and the page must re-evaluate its claims when that happens.
  const token = useSyncExternalStore(onSessionChange, () => getTokens()?.access_token ?? null);
  if (!token) return <Navigate to="/login" replace />;
  if (getClaims().mustChangePassword) return <ChangePasswordPage />;
  return <>{children}</>;
}

export default function App() {
  const signedIn = useSignedIn();
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={signedIn ? <Navigate to="/" replace /> : <LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/accept-invite" element={<ResetPasswordPage invite />} />
        <Route path="/business" element={<RequireAuth><BusinessPage /></RequireAuth>} />
        <Route path="/data" element={<RequireAuth><DataPage /></RequireAuth>} />
        <Route path="/accuracy" element={<RequireAuth><AccuracyPage /></RequireAuth>} />
        <Route path="/setup" element={<RequireAuth><SetupWizardPage /></RequireAuth>} />
        <Route path="/" element={<RequireAuth><Dashboard /></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><SettingsPage /></RequireAuth>} />
        <Route path="/alert/:alertId" element={<RequireAuth><AlertDetailPage /></RequireAuth>} />
      </Routes>
    </BrowserRouter>
  );
}
