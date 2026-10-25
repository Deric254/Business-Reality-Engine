import { useEffect, useRef, useState } from "react";

/* Series colours: one fixed hue per measure, everywhere. Teal, amber and blue were run through the palette validator
   (lightness band, chroma floor, colour-blind separation, contrast on white) and pass. Text never wears these colours;
   a coloured mark beside the text carries identity. */
export const C = {
  sales: "#0c9a88", spending: "#d1790a", profit: "#2c6fe0",
  muted: "#c9d2cc", grid: "#e2e6df", axis: "#67726c", text: "#16211e", textMuted: "#46524d", surface: "#ffffff", wash: "#f6f7f4",
  good: "#146c3e", bad: "#b42318",
};
export const HEAT = ["#e4f4f1", "#a9ded6", "#5cc2b3", "#16998a", "#0a5f54"];        // one hue, light to dark

export const fmtFull = (n: number) => Math.round(n).toLocaleString("en-US");
export const monthShort = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-KE", { month: "short" });
export const monthLong = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-KE", { month: "long", year: "numeric" });
export const dayShort = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-KE", { day: "numeric", month: "short" });
export const dayLong = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-KE", { weekday: "short", day: "numeric", month: "short", year: "numeric" });

/** Width of the element a chart lives in, tracked live, so text is drawn at real pixel size (readable on a phone). */
export function useWidth(min = 280) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const set = () => setW(Math.max(min, Math.floor(el.clientWidth)));
    const ro = new ResizeObserver(set);
    ro.observe(el); set();
    return () => ro.disconnect();
  }, [min]);
  return [ref, w] as const;
}

/** A clean axis: round steps, always containing zero. */
export function niceScale(minV: number, maxV: number, count = 4) {
  const lo = Math.min(0, minV), hi = Math.max(0, maxV, 1);
  const raw = (hi - lo) / count, mag = 10 ** Math.floor(Math.log10(raw)), norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const start = Math.floor(lo / step) * step, end = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return { min: start, max: end, ticks };
}

