import { SEVERITY_COLOR, SEVERITY_LABEL, SEVERITY_TEXT, SEVERITY_TINT } from "../lib/severity";

const SEVERITIES = ["critical", "high", "medium", "informational", "opportunity"];
const STATES = [
  { value: "", label: "All states" },
  { value: "detected", label: "New" },
  { value: "investigating", label: "Being looked at" },
  { value: "action_taken", label: "Action taken" },
  { value: "resolved", label: "Resolved" },
];

interface FilterBarProps {
  activeSeverities: Set<string>;
  onToggleSeverity: (severity: string) => void;
  state: string;
  onStateChange: (state: string) => void;
}

export function FilterBar({ activeSeverities, onToggleSeverity, state, onStateChange }: FilterBarProps) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <h2 className="text-base font-bold tracking-tight mr-2">Alerts</h2>
      {SEVERITIES.map((sev) => {
        const active = activeSeverities.has(sev);
        return (
          <button
            key={sev}
            onClick={() => onToggleSeverity(sev)}
            aria-pressed={active}
            className="text-xs font-medium px-3 py-1.5 rounded-full border transition-colors flex items-center gap-1.5"
            style={{
              borderColor: active ? SEVERITY_COLOR[sev] : "var(--color-field)",
              color: active ? SEVERITY_TEXT[sev] : "var(--color-ink-muted)",
              backgroundColor: active ? SEVERITY_TINT[sev] : "var(--color-panel)",
            }}
          >
            <span className="w-2 h-2 rounded-full" style={{ backgroundColor: active ? SEVERITY_COLOR[sev] : "var(--color-field)" }} aria-hidden />
            {SEVERITY_LABEL[sev]}
          </button>
        );
      })}
      <select
        aria-label="Filter by status"
        value={state}
        onChange={(e) => onStateChange(e.target.value)}
        className="text-sm bg-panel border border-field rounded-sm px-2.5 py-1.5 text-ink-text sm:ml-auto"
      >
        {STATES.map((s) => (<option key={s.value} value={s.value}>{s.label}</option>))}
      </select>
    </div>
  );
}
