import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, settingsApi, tenantApi, type TenantSettings } from "../api/client";
import { getClaims } from "../api/session";
import { Shell } from "../components/Shell";
import { CaptureForm } from "./DataPage";
import { Card, Notice, inputCls, primaryCls, btnCls } from "../components/ui";
import { setLang, useT, type Lang } from "../lib/i18n";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");
const ZONES = ["Africa/Nairobi", "Africa/Kampala", "Africa/Dar_es_Salaam", "Africa/Lagos", "Africa/Johannesburg", "Europe/London", "Asia/Dubai", "America/New_York", "UTC"];
const TOTAL = 3;

function Body({ tenantId }: { tenantId: string }) {
  const t = useT();
  const nav = useNavigate();
  const isOwner = getClaims().role === "owner";
  const [step, setStep] = useState(1);
  const [s, setS] = useState<TenantSettings | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  useEffect(() => { tenantApi.me().then(setS).catch((e) => setErr(errText(e))); }, []);

  async function saveBasics(e: FormEvent) {
    e.preventDefault(); if (!s) return; setErr(null);
    try { await tenantApi.update({ name: s.name, timezone: s.timezone, base_currency: s.base_currency, country: s.country, area: s.area, language: s.language }); setLang((s.language as Lang) === "sw" ? "sw" : "en"); setStep(2); }
    catch (x) { setErr(errText(x)); }
  }
  async function addChannel(e: FormEvent) {
    e.preventDefault(); setErr(null);
    try { await settingsApi.createChannel(tenantId, { channel_type: "email", target: email.trim(), min_severity: "high" }); setNotice("Alerts will be emailed to you."); setEmail(""); }
    catch (x) { setErr(errText(x)); }
  }

  if (!isOwner) return <p className="text-sm text-ink-muted">Only an owner can run setup.</p>;
  const input = `${inputCls} mt-1`;
  return (
    <>
      <div>
        <p className="text-xs text-ink-muted">{t("wiz.step", { n: step, total: TOTAL })}</p>
        <h1 className="display text-3xl sm:text-4xl">{t("wiz.title")}</h1>
        <div className="h-1 bg-panel mt-2 rounded-sm"><div className="h-full bg-info rounded-sm transition-all" style={{ width: `${(step / TOTAL) * 100}%` }} /></div>
      </div>
      <Notice error={err} notice={notice} />
      {step === 1 && s && (
        <Card>
          <form onSubmit={saveBasics} className="grid sm:grid-cols-2 gap-3">
            <label className="text-xs text-ink-muted sm:col-span-2">Business name<input required className={input} value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} /></label>
            <label className="text-xs text-ink-muted">Time zone (days start and end here)
              <select className={input} value={s.timezone} onChange={(e) => setS({ ...s, timezone: e.target.value })}>{[...new Set([s.timezone, ...ZONES])].map((z) => <option key={z}>{z}</option>)}</select></label>
            <label className="text-xs text-ink-muted">Main currency<input required maxLength={3} minLength={3} className={`${input} uppercase`} value={s.base_currency} onChange={(e) => setS({ ...s, base_currency: e.target.value.toUpperCase() })} /></label>
            <label className="text-xs text-ink-muted">Country (for public holidays, 2 letters)<input maxLength={2} className={`${input} uppercase`} value={s.country ?? ""} onChange={(e) => setS({ ...s, country: e.target.value.toUpperCase() || null })} placeholder="KE" /></label>
            <label className="text-xs text-ink-muted sm:col-span-2">Where your business is (area or city, used when you ask about competitors or the market)<input maxLength={100} className={input} value={s.area ?? ""} onChange={(e) => setS({ ...s, area: e.target.value || null })} placeholder="Westlands, Nairobi" /></label>
            <label className="text-xs text-ink-muted">Language<select className={input} value={s.language} onChange={(e) => setS({ ...s, language: e.target.value })}><option value="en">English</option><option value="sw">Kiswahili</option></select></label>
            <div className="sm:col-span-2"><button className={primaryCls}>{t("common.next")}</button></div>
          </form>
        </Card>
      )}
      {step === 2 && (
        <Card>
          <p className="text-sm mb-3">Bring in your numbers. If you have a system or spreadsheets, connect them; otherwise add a first entry by hand.</p>
          <Link to="/data" className={`${primaryCls} inline-block`}>Connect a data source</Link>
          <div className="mt-5"><div className="text-xs text-ink-muted mb-2">Or add one entry now</div><CaptureForm tenantId={tenantId} /></div>
          <div className="flex gap-2 mt-5"><button className={btnCls} onClick={() => setStep(1)}>{t("common.back")}</button><button className={primaryCls} onClick={() => setStep(3)}>{t("common.next")}</button></div>
        </Card>
      )}
      {step === 3 && (
        <Card>
          <p className="text-sm mb-3">Where should urgent alerts go? You can add Slack, Teams, SMS and WhatsApp later in Settings.</p>
          <form onSubmit={addChannel} className="flex flex-wrap gap-2 items-end">
            <label className="text-xs text-ink-muted flex-1 min-w-[220px]">Your email<input type="email" required className={input} value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <button className={primaryCls}>{t("common.add")}</button>
          </form>
          <div className="flex gap-2 mt-5"><button className={btnCls} onClick={() => setStep(2)}>{t("common.back")}</button><button className={primaryCls} onClick={() => nav("/business")}>{t("common.done")}</button><button className={btnCls} onClick={() => nav("/")}>{t("common.skip")}</button></div>
        </Card>
      )}
    </>
  );
}

export function SetupWizardPage() {
  return <Shell>{(tenant) => <Body tenantId={tenant.id} />}</Shell>;
}
