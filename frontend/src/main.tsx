import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";

// Installable and quick to open; the worker never touches the API (see public/sw.js). Production only, so development
// never serves a stale page. A failure to register just means no install prompt.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
}

// The owner's CRM opens the licence page with a one-time pairing key in the part of the address after "#" (never sent to any server or logged).
// It is kept for the licence page to use once the owner is signed in, and removed from the address bar at once.
if (window.location.hash.startsWith("#feed=")) {
  try { sessionStorage.setItem("bre.pendingFeed", window.location.hash.slice(6)); } catch { /* the page still works; the CRM can pair next time */ }
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
