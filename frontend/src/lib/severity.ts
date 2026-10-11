export const SEVERITY_COLOR: Record<string, string> = {
  critical: "#e5484d",
  high: "#f0883e",
  medium: "#e8c547",
  informational: "#5b9bd5",
  opportunity: "#4caf7d",
};

export const SEVERITY_LABEL: Record<string, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  informational: "Informational",
  opportunity: "Opportunity",
};

export const COVERAGE_COLOR: Record<string, string> = {
  live: "#4caf7d",
  recent: "#5b9bd5",
  stale: "#e8c547",
  not_connected: "#8b93a1",
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
