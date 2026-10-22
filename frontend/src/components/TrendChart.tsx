import { useEffect, useMemo, useRef, useState } from "react";
import type { TrendDay } from "../api/client";
import { compact } from "./ui";

const SALES = "#1b5a54";
const EXPENSES = "#c4730b";
const PREV = "#8c968f";

const day = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-KE", { day: "numeric", month: "short" });

/** Daily sales and expenses for the period, with the previous period's sales as a faint reference line.
 *  Drawn at real pixel width so text stays readable on phones. */
export function TrendChart({ current, previous, height = 260 }: { current: TrendDay[]; previous: TrendDay[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(280, el.clientWidth)));
    ro.observe(el); setW(Math.max(280, el.clientWidth));
    return () => ro.disconnect();
  }, []);

  const geo = useMemo(() => {
    const padL = 46, padR = 12, padT = 12, padB = 26;
    const n = current.length;
    const max = Math.max(1, ...current.map((d) => Math.max(d.sales, d.expenses)), ...previous.map((d) => d.sales)) * 1.08;
    const x = (i: number) => padL + (n <= 1 ? 0 : (i * (w - padL - padR)) / (n - 1));
    const y = (v: number) => padT + (1 - v / max) * (height - padT - padB);
    const line = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
    return { padL, padR, padT, padB, n, max, x, y, line };
  }, [current, previous, w, height]);

  const { padL, padR, padT, padB, n, max, x, y, line } = geo;
  const salesPath = line(current.map((d) => d.sales));
  const area = `${salesPath}L${x(n - 1).toFixed(1)} ${y(0)}L${x(0).toFixed(1)} ${y(0)}Z`;
  const ticks = [0, 0.5, 1].map((f) => max / 1.08 * f);
  const total = current.reduce((s, d) => s + d.sales, 0);
  const hd = hover != null ? current[hover] : null;

  function move(e: React.PointerEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    const i = Math.round(((px - padL) / Math.max(1, w - padL - padR)) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  }

  return (
    <div ref={ref} className="relative">
      <svg width={w} height={height} role="img" aria-label={`Daily sales and spending for the last ${n} days. Total sales ${Math.round(total).toLocaleString("en-KE")}.`}
        onPointerMove={move} onPointerLeave={() => setHover(null)} className="block touch-pan-y">
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} stroke="#e2e6df" strokeWidth="1" />
            <text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="11.5" fill="#67726c">{compact(t)}</text>
          </g>
        ))}
        {[0, Math.floor((n - 1) / 3), Math.floor((2 * (n - 1)) / 3), n - 1].map((i, k) => (
          <text key={k} x={x(i)} y={height - 6} textAnchor={k === 0 ? "start" : k === 3 ? "end" : "middle"} fontSize="11.5" fill="#67726c">{day(current[i].date)}</text>
        ))}
        <path d={area} fill={SALES} opacity="0.08" />
        <path d={line(previous.map((d) => d.sales))} fill="none" stroke={PREV} strokeWidth="1.5" strokeDasharray="4 4" />
        <path d={line(current.map((d) => d.expenses))} fill="none" stroke={EXPENSES} strokeWidth="2" strokeLinejoin="round" />
        <path d={salesPath} fill="none" stroke={SALES} strokeWidth="2.75" strokeLinejoin="round" strokeLinecap="round" pathLength={1} className="draw" />
        {hd && hover != null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={padT} y2={height - padB} stroke="#16211e" strokeOpacity="0.25" />
            <circle cx={x(hover)} cy={y(hd.sales)} r="4.5" fill={SALES} stroke="#fff" strokeWidth="2" />
            <circle cx={x(hover)} cy={y(hd.expenses)} r="4" fill={EXPENSES} stroke="#fff" strokeWidth="2" />
          </g>
        )}
      </svg>
      {hd && hover != null && (
        <div className="pointer-events-none absolute top-1 z-10 rounded-sm border border-border bg-panel px-3 py-2 text-xs shadow-sm"
          style={{ left: Math.min(Math.max(x(hover) + 12, 8), w - 168) }}>
          <div className="font-semibold text-ink-text mb-1">{day(hd.date)}</div>
          <div className="flex justify-between gap-6"><span style={{ color: SALES }}>Sales</span><span className="kpi-figure">{Math.round(hd.sales).toLocaleString("en-KE")}</span></div>
          <div className="flex justify-between gap-6"><span style={{ color: EXPENSES }}>Spending</span><span className="kpi-figure">{Math.round(hd.expenses).toLocaleString("en-KE")}</span></div>
        </div>
      )}
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-muted mt-1 pl-[46px]">
        <span className="inline-flex items-center gap-1.5"><span className="inline-block w-4 h-[3px] rounded" style={{ background: SALES }} />Sales</span>
        <span className="inline-flex items-center gap-1.5"><span className="inline-block w-4 h-[2px] rounded" style={{ background: EXPENSES }} />Spending</span>
        <span className="inline-flex items-center gap-1.5"><span className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: PREV }} />Sales in the period before</span>
      </div>
    </div>
  );
}
