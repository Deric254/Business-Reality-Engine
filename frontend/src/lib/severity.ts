// Strong colour (dots, borders), readable text colour, and soft tint per severity. Text colours pass 4.5:1 on their tint.
export const SEVERITY_COLOR: Record<string, string> = {
  critical: "#d92d20",
  high: "#e8590c",
  medium: "#d4a017",
  informational: "#2f6db5",
  opportunity: "#1f9254",
};

export const SEVERITY_TEXT: Record<string, string> = {
  critical: "#b42318",
  high: "#a8400a",
  medium: "#8a5a00",
  informational: "#1b4f8a",
  opportunity: "#146c3e",
};

export const SEVERITY_TINT: Record<string, string> = {
  critical: "#fef3f2",
  high: "#fff4ec",
  medium: "#fef8e1",
  informational: "#eef4fb",
  opportunity: "#edf8f1",
};

export const SEVERITY_LABEL: Record<string, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  informational: "Info",
  opportunity: "Opportunity",
};

export const COVERAGE_COLOR: Record<string, string> = {
  live: "#1f9254",
  recent: "#2f6db5",
  stale: "#d4a017",
  not_connected: "#8a9390",
};

// The API stores naive UTC timestamps (no "Z"). JS parses a zone-less ISO string
// as LOCAL time, which skewed every "x ago" by the viewer's UTC offset (3h in
// Kenya). Treat zone-less values as UTC.
export function parseApiDate(iso: string): Date {
  return new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
}

export function timeAgo(iso: string): string {
  const diffMs = Date.now() - parseApiDate(iso).getTime();
  const mins = Math.max(0, Math.floor(diffMs / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function formatKes(value: number): string {
  return new Intl.NumberFormat("en-KE", { maximumFractionDigits: 0 }).format(value);
}
