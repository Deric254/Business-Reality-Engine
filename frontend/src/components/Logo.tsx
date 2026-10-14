/** Business Reality mark: a stepped line rising to one highlighted point (the thing worth noticing). */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#1b4f8a" />
      <path d="M6 22h5v-5h5v-4h5v-4" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="24.5" cy="9" r="3" fill="#f5b82e" />
    </svg>
  );
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark size={size} />
      <span className="text-base font-bold tracking-tight text-ink-text">Business Reality</span>
    </span>
  );
}
