import { Suspense, lazy, useSyncExternalStore, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { AlertsPage } from "./pages/AlertsPage";
import { LoginPage } from "./pages/LoginPage";
import { getClaims, getTokens, isSignedIn, onSessionChange } from "./api/session";

const AlertDetailPage = lazy(() => import("./pages/AlertDetailPage").then((m) => ({ default: m.AlertDetailPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const BusinessPage = lazy(() => import("./pages/BusinessPage").then((m) => ({ default: m.BusinessPage })));
const DataPage = lazy(() => import("./pages/DataPage").then((m) => ({ default: m.DataPage })));
const AccuracyPage = lazy(() => import("./pages/AccuracyPage").then((m) => ({ default: m.AccuracyPage })));
const SetupWizardPage = lazy(() => import("./pages/SetupWizardPage").then((m) => ({ default: m.SetupWizardPage })));
const ForgotPasswordPage = lazy(() => import("./pages/ForgotPasswordPage").then((m) => ({ default: m.ForgotPasswordPage })));
const RecoverPage = lazy(() => import("./pages/RecoverPage").then((m) => ({ default: m.RecoverPage })));
const ResetPasswordPage = lazy(() => import("./pages/ResetPasswordPage").then((m) => ({ default: m.ResetPasswordPage })));
const ChangePasswordPage = lazy(() => import("./pages/ChangePasswordPage").then((m) => ({ default: m.ChangePasswordPage })));

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
      <Suspense fallback={<div className="min-h-screen" aria-busy="true" />}>
      <Routes>
        <Route path="/login" element={signedIn ? <Navigate to="/" replace /> : <LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/recover" element={<RecoverPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/accept-invite" element={<ResetPasswordPage invite />} />
        <Route path="/alerts" element={<RequireAuth><AlertsPage /></RequireAuth>} />
        <Route path="/business" element={<RequireAuth><BusinessPage /></RequireAuth>} />
        <Route path="/data" element={<RequireAuth><DataPage /></RequireAuth>} />
        <Route path="/accuracy" element={<RequireAuth><AccuracyPage /></RequireAuth>} />
        <Route path="/setup" element={<RequireAuth><SetupWizardPage /></RequireAuth>} />
        <Route path="/" element={<RequireAuth><Dashboard /></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><SettingsPage /></RequireAuth>} />
        <Route path="/alert/:alertId" element={<RequireAuth><AlertDetailPage /></RequireAuth>} />
      </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
