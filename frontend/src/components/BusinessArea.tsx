import { useEffect, useState, type FormEvent } from "react";
import { ApiError, tenantApi } from "../api/client";
import { inputCls, primaryCls } from "./ui";

/** Where the business trades. Added to web searches so "who are my competitors" means the competitors nearby. */
export function BusinessArea() {
  const [area, setArea] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  useEffect(() => { tenantApi.me().then((t) => { setArea(t.area ?? ""); setSaved(t.area ?? ""); }).catch(() => undefined); }, []);

  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null); setOk(false);
    try { const t = await tenantApi.update({ area: area.trim() || null }); setArea(t.area ?? ""); setSaved(t.area ?? ""); setOk(true); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "Something went wrong. Try again."); }
    finally { setBusy(false); }
  }

  return (
    <section aria-labelledby="area">
      <h2 id="area" className="text-base font-semibold mb-1">Where your business is</h2>
      <p className="text-sm text-ink-muted mb-4">Your area or city. When you ask the Ask box about competitors or the market, it is added to the web search. Your sales and other numbers are never sent.</p>
      <form onSubmit={save} className="flex gap-2 max-w-xl">
        <input aria-label="Business area or city" maxLength={100} value={area} onChange={(e) => { setArea(e.target.value); setOk(false); }} placeholder="Westlands, Nairobi" className={inputCls} />
        <button disabled={busy || area.trim() === saved} className={`${primaryCls} px-5`}>{busy ? "Saving…" : "Save"}</button>
      </form>
      {err && <p role="alert" className="text-sm text-critical mt-2">{err}</p>}
      {ok && <p role="status" className="text-sm text-opportunity mt-2">Saved.</p>}
    </section>
  );
}
