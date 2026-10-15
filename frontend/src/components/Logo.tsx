export const BRAND = "DericBI";
export const SLOGAN = "Cut through noise";

/** The DericBI badge. It already contains the name and slogan, so it is shown large and never recoloured. */
export function LogoMark({ size = 40 }: { size?: number }) {
  return <img src="/logo.png" width={size} height={size} alt={BRAND} className="shrink-0 select-none" draggable={false} />;
}

/** `stacked` = big badge with the slogan underneath (sidebar, sign-in). Default = compact badge beside the name. */
export function Logo({ size = 40, light = false, stacked = false }: { size?: number; light?: boolean; stacked?: boolean }) {
  if (stacked) return <LogoMark size={size} />;
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark size={size} />
      <span className="leading-tight">
        <span className={`display block text-lg ${light ? "text-white" : "text-ink-text"}`}>{BRAND}</span>
        <span className={`block text-xs ${light ? "text-white/80" : "text-ink-faint"}`}>{SLOGAN}</span>
      </span>
    </span>
  );
}
