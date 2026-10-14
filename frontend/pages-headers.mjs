// Run after `vite build` on Cloudflare Pages: writes _headers (security headers + CSP that allows
// only this API origin) and _redirects (SPA routing) into dist/.
import { writeFileSync } from "node:fs";
const api = (process.env.VITE_API_BASE || "").replace(/\/+$/, "");
if (!/^https:\/\/[a-z0-9.-]+(:\d+)?$/i.test(api)) {
  console.error("VITE_API_BASE must be the https origin of the API, e.g. https://your-service.onrender.com");
  process.exit(1);
}
const csp = `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' ${api}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`;
writeFileSync("dist/_headers", `/*\n  Content-Security-Policy: ${csp}\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  X-Frame-Options: DENY\n/assets/*\n  Cache-Control: public, max-age=31536000, immutable\n`);
writeFileSync("dist/_redirects", "/*  /index.html  200\n");
console.log("wrote dist/_headers and dist/_redirects for", api);
