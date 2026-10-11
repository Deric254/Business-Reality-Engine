import { useSyncExternalStore, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { AlertDetailPage } from "./pages/AlertDetailPage";
import { SettingsPage } from "./pages/SettingsPage";
import { LoginPage } from "./pages/LoginPage";
import { isSignedIn, onSessionChange } from "./api/session";

function useSignedIn() {
  return useSyncExternalStore(onSessionChange, isSignedIn);
}

function RequireAuth({ children }: { children: ReactNode }) {
  return useSignedIn() ? <>{children}</> : <Navigate to="/login" replace />;
}

export default function App() {
  const signedIn = useSignedIn();
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={signedIn ? <Navigate to="/" replace /> : <LoginPage />} />
        <Route path="/" element={<RequireAuth><Dashboard /></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><SettingsPage /></RequireAuth>} />
        <Route path="/alert/:alertId" element={<RequireAuth><AlertDetailPage /></RequireAuth>} />
      </Routes>
    </BrowserRouter>
  );
}
