import { useCallback, useEffect, useState } from "react";
import { ApiError, demoApi, type SampleKind } from "../api/client";
import { Card, btnCls } from "./ui";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");

/** For a business with no data yet: try a sample business to see the app working. Once one is loaded, say so plainly and
 *  offer to remove it. Shown to owners only, and only while it is useful (no data yet, or the sample itself is there). */
export function SampleData({ tenantId, hasData, onChanged, setNotice, setError }: {
  tenantId: string; hasData: boolean; onChanged: () => void; setNotice: (s: string | null) => void; setError: (s: string | null) => void;
}) {
  const [samples, setSamples] = useState<SampleKind[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(() => demoApi.list(tenantId).then((r) => { setSamples(r.samples); setLoaded(r.loaded); }).catch(() => undefined), [tenantId]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function act(fn: () => Promise<string>) {
    setBusy(true); setError(null); setNotice(null);
    try { setNotice(await fn()); await refresh(); onChanged(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  if (loaded) {
    return (
      <Card className="!p-5 space-y-3">
        <p className="font-semibold">You are looking at sample data.</p>
        <p className="text-sm text-ink-muted">It is made up, so nothing here describes your business. Remove it before connecting your own data; it can only be removed while it is the only data here.</p>
        <button className={btnCls} disabled={busy} onClick={() => { if (window.confirm("Remove the sample data?")) void act(async () => { await demoApi.remove(tenantId); return "Sample data removed."; }); }}>Remove sample data</button>
      </Card>
    );
  }
  if (hasData || samples.length === 0) return null;
  return (
    <Card className="!p-5 space-y-3">
      <div><p className="font-semibold">Want to see it working first?</p><p className="text-sm text-ink-muted mt-1">Load a made-up business with four months of activity and a problem hiding in it. You can remove it later.</p></div>
      <div className="grid sm:grid-cols-3 gap-3">
        {samples.map((s) => (
          <button key={s.id} disabled={busy} onClick={() => void act(async () => { const r = await demoApi.load(tenantId, s.id); return `Loaded the ${s.title.toLowerCase()} sample: ${r.events.toLocaleString("en-US")} records and ${r.alerts} alerts to look at.`; })}
            className="text-left border border-border rounded-xl p-4 bg-panel hover:border-info hover:bg-info-soft/40 transition-colors disabled:opacity-60">
            <div className="font-semibold">{s.title}</div><div className="text-sm text-ink-muted mt-1">{s.blurb}</div>
          </button>
        ))}
      </div>
    </Card>
  );
}
