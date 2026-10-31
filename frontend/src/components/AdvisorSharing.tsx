import { useEffect, useState } from "react";
import { ApiError, tenantApi } from "../api/client";

/** A business's own switch for letting the person who set it up hear about serious problems. Off until the owner turns it on. */
export function AdvisorSharing() {
  const [on, setOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { tenantApi.me().then((t) => setOn(t.share_alerts_with_advisor)).catch(() => undefined); }, []);

  async function change(next: boolean) {
    setBusy(true); setErr(null);
    try { const t = await tenantApi.update({ share_alerts_with_advisor: next }); setOn(t.share_alerts_with_advisor); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "Something went wrong. Try again."); }
    finally { setBusy(false); }
  }

  if (on === null) return null;
  return (
    <section aria-labelledby="advisor">
      <h2 id="advisor" className="text-base font-semibold mb-1">Tell my DericBI advisor about serious problems</h2>
      <p className="text-sm text-ink-muted mb-3">
        Only if this was set up for you by DericBI. When it is on, they see the headline of your critical or high alerts (for example "Westlands sales are down 15%", so it can name a branch or product and include a percentage) so they can call you.
        They never see your sales, customers or other records. It is off until you turn it on, and you can turn it off at any time.
      </p>
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={on} disabled={busy} onChange={(e) => change(e.target.checked)} />
        Share serious alert headlines with my advisor
      </label>
      {err && <p role="alert" className="text-sm text-critical mt-2">{err}</p>}
    </section>
  );
}
