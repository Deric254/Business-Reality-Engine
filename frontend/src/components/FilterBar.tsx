import { SEVERITY_COLOR, SEVERITY_LABEL } from "../lib/severity";

const SEVERITIES = ["critical", "high", "medium", "informational", "opportunity"];
const STATES = [
  { value: "", label: "All states" },
  { value: "detected", label: "Detected" },
  { value: "investigating", label: "Investigating" },
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
    <div className="flex items-center gap-2 flex-wrap py-3 px-1">
      {SEVERITIES.map((sev) => {
        const active = activeSeverities.has(sev);
        const color = SEVERITY_COLOR[sev];
        return (
          <button
            key={sev}
            onClick={() => onToggleSeverity(sev)}
            aria-pressed={active}
            className="text-xs px-2.5 py-1 rounded-sm border transition-colors flex items-center gap-1.5"
            style={{
              borderColor: active ? color : "var(--color-border)",
              color: active ? color : "var(--color-ink-muted)",
              backgroundColor: active ? `${color}1a` : "transparent",
            }}
          >
            <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} aria-hidden />
            {SEVERITY_LABEL[sev]}
          </button>
        );
      })}
      <div className="w-px h-5 bg-border mx-1" aria-hidden />
      <select
        value={state}
        onChange={(e) => onStateChange(e.target.value)}
        className="text-xs bg-transparent border border-border rounded-sm px-2 py-1 text-ink-muted focus:text-ink-text"
      >
        {STATES.map((s) => (
          <option key={s.value} value={s.value} className="bg-panel">
            {s.label}
          </option>
        ))}
      </select>
    </div>
  );
}
