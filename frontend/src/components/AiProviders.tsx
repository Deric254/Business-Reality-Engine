import { useCallback, useEffect, useState, type FormEvent } from "react";
import { aiApi, ApiError, type AiProvider } from "../api/client";
import { btnCls, inputCls, primaryCls } from "./ui";

type Preset = { name: string; kind: string; base_url: string; model: string; keyUrl: string; free: string };
const PRESETS: Preset[] = [
  { name: "Google Gemini", kind: "openai_compatible", base_url: "https://generativelanguage.googleapis.com/v1beta/openai/", model: "gemini-3.8-flash", keyUrl: "https://aistudio.google.com/apikey", free: "Free tier" },
  { name: "Groq", kind: "openai_compatible", base_url: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile", keyUrl: "https://console.groq.com/keys", free: "Free tier" },
  { name: "NVIDIA", kind: "openai_compatible", base_url: "https://integrate.api.nvidia.com/v1", model: "meta/llama-3.3-70b-instruct", keyUrl: "https://build.nvidia.com", free: "Free credits" },
  { name: "OpenRouter", kind: "openai_compatible", base_url: "https://openrouter.ai/api/v1", model: "", keyUrl: "https://openrouter.ai/keys", free: "Has free models (name ends in :free)" },
  { name: "Grok (xAI)", kind: "openai_compatible", base_url: "https://api.x.ai/v1", model: "grok-4", keyUrl: "https://console.x.ai", free: "Paid or credits" },
  { name: "OpenAI", kind: "openai", base_url: "", model: "gpt-4o-mini", keyUrl: "https://platform.openai.com/api-keys", free: "Paid" },
  { name: "Claude (Anthropic)", kind: "anthropic", base_url: "", model: "", keyUrl: "https://console.anthropic.com/settings/keys", free: "Paid" },
  { name: "Other (OpenAI-compatible)", kind: "openai_compatible", base_url: "", model: "", keyUrl: "", free: "" },
];

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");

export function AiProviders({ tenantId, isOwner }: { tenantId: string; isOwner: boolean }) {
  const [rows, setRows] = useState<AiProvider[]>([]);
  const [pick, setPick] = useState(0);
  const [key, setKey] = useState("");
  const [model, setModel] = useState(PRESETS[0].model);
  const [baseUrl, setBaseUrl] = useState(PRESETS[0].base_url);
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const preset = PRESETS[pick];
  const custom = preset.name.startsWith("Other");

  const load = useCallback(() => { aiApi.list(tenantId).then(setRows).catch((e) => setErr(msg(e))); }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  function choose(i: number) {
    setPick(i); setModels([]); setModel(PRESETS[i].model); setBaseUrl(PRESETS[i].base_url); setErr(null); setOk(null);
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null); setOk(null);
    try {
      await aiApi.add(tenantId, { label: preset.name, kind: preset.kind, api_key: key, model, base_url: baseUrl || undefined });
      setKey(""); setOk(`${preset.name} is connected and answered a test request.`); load();
    } catch (x) { setErr(msg(x)); } finally { setBusy(false); }
  }

  async function loadModels() {
    setLoadingModels(true); setErr(null); setOk(null);
    try {
      const r = await aiApi.models(tenantId, { kind: preset.kind, api_key: key, base_url: baseUrl || undefined });
      setModels(r.models);
      if (r.models.length && !r.models.includes(model)) setModel(r.models.find((m) => /flash|mini|instant|small|haiku/i.test(m)) ?? r.models[0]);
      if (!r.models.length) setErr("The provider returned no models. Type the model name instead.");
    } catch (x) { setErr(msg(x)); } finally { setLoadingModels(false); }
  }

  async function remove(r: AiProvider) {
    setErr(null); setOk(null);
    try { await aiApi.remove(tenantId, r.id); load(); } catch (x) { setErr(msg(x)); }
  }

  return (
    <div className="space-y-4">
      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted bg-panel border border-border rounded-xl px-4 py-3">No AI connected here yet. Until you add one, the app uses whatever key is set on the server, if any.</p>
      ) : (
        <ul className="divide-y divide-border border border-border rounded-xl bg-panel">
          {rows.map((r, i) => (
            <li key={r.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="font-medium">{r.label}{i === 0 ? <span className="ml-2 text-xs text-ink-faint">tried first</span> : <span className="ml-2 text-xs text-ink-faint">backup {i}</span>}</div>
                <div className="text-xs text-ink-muted truncate">{r.model} · key saved (hidden)</div>
              </div>
              {isOwner && <button className={btnCls} onClick={() => remove(r)}>Remove</button>}
            </li>
          ))}
        </ul>
      )}
      {rows.length > 1 && <p className="text-xs text-ink-muted">If the first one is busy or out of free usage, the next is used automatically.</p>}

      {isOwner ? (
        <form onSubmit={add} className="border border-border rounded-xl bg-panel p-4 space-y-3">
          <div className="font-medium text-sm">Connect an AI</div>
          <label className="block text-sm">Provider
            <select className={`${inputCls} mt-1`} value={pick} onChange={(e) => choose(Number(e.target.value))}>
              {PRESETS.map((p, i) => <option key={p.name} value={i}>{p.name}{p.free ? ` — ${p.free}` : ""}</option>)}
            </select>
          </label>
          {preset.keyUrl && <p className="text-xs text-ink-muted">Get a key at <a className="underline" href={preset.keyUrl} target="_blank" rel="noreferrer">{preset.keyUrl.replace("https://", "")}</a></p>}
          <label className="block text-sm">API key
            <input className={`${inputCls} mt-1`} type="password" autoComplete="off" required minLength={8} value={key} onChange={(e) => setKey(e.target.value)} placeholder="Paste the key here" />
          </label>
          <div className="block text-sm">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor="ai-model">Model {preset.kind === "anthropic" && <span className="text-ink-faint">(optional)</span>}</label>
              <button type="button" className="text-xs underline text-ink-muted disabled:opacity-50" disabled={loadingModels || key.length < 8} onClick={loadModels}>
                {loadingModels ? "Loading…" : "Load the models my key can use"}
              </button>
            </div>
            {models.length > 0 ? (
              <select id="ai-model" className={`${inputCls} mt-1`} value={model} onChange={(e) => setModel(e.target.value)}>
                {models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : (
              <input id="ai-model" className={`${inputCls} mt-1`} value={model} required={preset.kind === "openai_compatible"} onChange={(e) => setModel(e.target.value)} placeholder="Model names change often. Use the button above to see current ones." />
            )}
          </div>
          {(custom || preset.kind === "openai_compatible") && (
            <label className="block text-sm">Base URL
              <input className={`${inputCls} mt-1`} type="url" required value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://..." readOnly={!custom} />
            </label>
          )}
          {err && <p role="alert" className="text-sm text-critical">{err}</p>}
          {ok && <p role="status" className="text-sm text-opportunity">{ok}</p>}
          <button className={primaryCls} disabled={busy || !key}>{busy ? "Testing the key…" : "Test and save"}</button>
          <p className="text-xs text-ink-muted">The key is tested first, then stored encrypted. It is never shown again.</p>
        </form>
      ) : <p className="text-sm text-ink-muted">Only the owner can add or remove AI keys.</p>}
    </div>
  );
}
