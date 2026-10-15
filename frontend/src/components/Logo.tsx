/** Mark: a stepped line that ends on one highlighted point, the thing worth noticing. */
export function LogoMark({ size = 28, light = false }: { size?: number; light?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect width="32" height="32" rx="9" fill={light ? "#ffffff" : "#1b5a54"} />
      <path d="M6 22h5v-5h5v-4h5v-4" stroke={light ? "#1b5a54" : "#fff"} strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="24.5" cy="9" r="3" fill="#f2b632" />
    </svg>
  );
}

export function Logo({ size = 28, light = false }: { size?: number; light?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark size={size} light={light} />
      <span className={`display text-lg ${light ? "text-white" : "text-ink-text"}`}>Business Reality</span>
    </span>
  );
}
