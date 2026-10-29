import { useMemo, useState, type ReactNode } from "react";
import type { AMonth, AProjection, AWeek } from "../api/client";
import { compact } from "./ui";
import { C, HEAT, dayLong, dayShort, fmtFull, monthLong, monthShort, niceScale, useWidth } from "../lib/chartKit";

/** A column with a rounded data end (4px) and a square baseline end. */
function column(x: number, top: number, base: number, w: number) {
  const h = Math.abs(base - top);
  if (h < 0.5) return "";
  const r = Math.min(4, w / 2, h), up = top < base;
  return up
    ? `M${x} ${base}V${top + r}Q${x} ${top} ${x + r} ${top}H${x + w - r}Q${x + w} ${top} ${x + w} ${top + r}V${base}Z`
    : `M${x} ${base}V${top - r}Q${x} ${top} ${x + r} ${top}H${x + w - r}Q${x + w} ${top} ${x + w} ${top - r}V${base}Z`;
}

export function Tip({ left, top = 4, children, width = 190 }: { left: number; top?: number; children: ReactNode; width?: number }) {
  return (
    <div role="status" className="pointer-events-none absolute z-10 rounded-sm border border-border bg-panel px-3 py-2 text-xs shadow-sm" style={{ left, top, width }}>
      {children}
    </div>
  );
}
const TipRow = ({ color, label, value }: { color?: string; label: string; value: string }) => (
  <div className="flex items-center justify-between gap-4 py-px">
    <span className="inline-flex items-center gap-1.5 text-ink-muted">{color && <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: color }} />}{label}</span>
    <span className="kpi-figure text-ink-text">{value}</span>
  </div>
);

export function Legend({ items }: { items: { color: string; label: string; kind?: "bar" | "line" | "band" }[] }) {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-muted mt-2">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          {i.kind === "line" ? <span className="inline-block w-4 h-[2px] rounded" style={{ background: i.color }} />
            : i.kind === "band" ? <span className="inline-block w-4 h-2.5 rounded-sm" style={{ background: i.color, opacity: 0.2 }} />
            : <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: i.color }} />}
          {i.label}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ monthly: sales and spending columns, profit line */

export function MonthlyChart({ rows, cur }: { rows: AMonth[]; cur: string }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const h = 300, padL = 54, padR = 12, padT = 14, padB = 30;
  const n = rows.length;
  const sc = useMemo(() => niceScale(Math.min(0, ...rows.map((r) => r.profit)), Math.max(1, ...rows.flatMap((r) => [r.sales, r.spending, r.profit]))), [rows]);
  const y = (v: number) => padT + (1 - (v - sc.min) / (sc.max - sc.min)) * (h - padT - padB);
  const band = (w - padL - padR) / Math.max(1, n), bw = Math.min(24, band * 0.34);
  const cx = (i: number) => padL + band * (i + 0.5);
  const every = band < 34 ? 2 : 1;
  const line = rows.map((r, i) => `${i ? "L" : "M"}${cx(i).toFixed(1)} ${y(r.profit).toFixed(1)}`).join("");
  const hd = hover != null ? rows[hover] : null;
  const move = (e: React.PointerEvent<SVGSVGElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    setHover(Math.max(0, Math.min(n - 1, Math.floor((px - padL) / band))));
  };
  return (
    <div ref={ref} className="relative min-w-0 overflow-hidden">
      <svg width={w} height={h} role="img" aria-label={`Sales, spending and profit for each of the last ${n} months. Use the table view for exact figures.`}
        onPointerMove={move} onPointerLeave={() => setHover(null)} className="block touch-pan-y">
        {sc.ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} stroke={t === 0 ? C.axis : C.grid} strokeOpacity={t === 0 ? 0.5 : 1} />
            <text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="11.5" fill={C.axis}>{compact(t)}</text>
          </g>
        ))}
        {hover != null && <rect x={padL + band * hover} y={padT} width={band} height={h - padT - padB} fill={C.wash} />}
        {rows.map((r, i) => (
          <g key={r.month} opacity={r.partial ? 0.55 : 1}>
            <path d={column(cx(i) - bw - 1, y(r.sales), y(0), bw)} fill={C.sales} />
            <path d={column(cx(i) + 1, y(r.spending), y(0), bw)} fill={C.spending} />
          </g>
        ))}
        <path d={line} fill="none" stroke={C.profit} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {rows.map((r, i) => <circle key={r.month} cx={cx(i)} cy={y(r.profit)} r="4" fill={C.profit} stroke={C.surface} strokeWidth="2" />)}
        {rows.map((r, i) => (i % every === 0 || i === n - 1) && (
          <text key={r.month} x={cx(i)} y={h - 8} textAnchor="middle" fontSize="11.5" fill={C.axis}>
            {monthShort(r.month)}{(i === 0 || r.month.slice(5, 7) === "01") ? ` ${r.month.slice(2, 4)}` : ""}
          </text>
        ))}
      </svg>
      {hd && hover != null && (
        <Tip left={Math.min(Math.max(cx(hover) + 14, 8), w - 200)}>
          <div className="font-semibold text-ink-text mb-1">{monthLong(hd.month)}{hd.partial ? " (so far)" : ""}</div>
          <TipRow color={C.sales} label="Sales" value={`${cur} ${fmtFull(hd.sales)}`} />
          <TipRow color={C.spending} label="Spending" value={`${cur} ${fmtFull(hd.spending)}`} />
          <TipRow color={C.profit} label="Profit" value={`${cur} ${fmtFull(hd.profit)}`} />
          <TipRow label="Margin" value={hd.margin_pct == null ? "no sales" : `${hd.margin_pct.toFixed(0)}%`} />
        </Tip>
      )}
      <Legend items={[{ color: C.sales, label: "Sales" }, { color: C.spending, label: "Spending" }, { color: C.profit, label: "Profit", kind: "line" }]} />
      {rows.some((r) => r.partial) && <p className="text-xs text-ink-faint mt-1">The lighter columns are the month still in progress.</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ margin line */

export function MarginChart({ rows }: { rows: AMonth[] }) {
  const [ref, w] = useWidth(240);
  const [hover, setHover] = useState<number | null>(null);
  const pts = rows.map((r, i) => ({ i, v: r.margin_pct, m: r.month, partial: r.partial })).filter((p): p is { i: number; v: number; m: string; partial: boolean } => p.v != null);
  const h = 220, padL = 40, padR = 18, padT = 14, padB = 28;
  const sc = niceScale(Math.min(0, ...pts.map((p) => p.v)), Math.max(10, ...pts.map((p) => p.v)), 4);
  const x = (i: number) => padL + (rows.length <= 1 ? 0 : (i * (w - padL - padR)) / (rows.length - 1));
  const y = (v: number) => padT + (1 - (v - sc.min) / (sc.max - sc.min)) * (h - padT - padB);
  const path = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)} ${y(p.v).toFixed(1)}`).join("");
  const last = pts[pts.length - 1];
  const hp = hover != null ? pts.find((p) => p.i === hover) : null;
  const move = (e: React.PointerEvent<SVGSVGElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    setHover(Math.max(0, Math.min(rows.length - 1, Math.round(((px - padL) / Math.max(1, w - padL - padR)) * (rows.length - 1)))));
  };
  if (pts.length < 2) return <p className="text-sm text-ink-muted py-8 text-center">A margin trend needs at least two months with sales.</p>;
  return (
    <div ref={ref} className="relative min-w-0 overflow-hidden">
      <svg width={w} height={h} role="img" aria-label={`Profit margin by month, latest ${last.v.toFixed(0)} percent.`} onPointerMove={move} onPointerLeave={() => setHover(null)} className="block touch-pan-y">
        {sc.ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} stroke={t === 0 ? C.axis : C.grid} strokeOpacity={t === 0 ? 0.5 : 1} />
            <text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="11.5" fill={C.axis}>{t}%</text>
          </g>
        ))}
        <path d={`${path}L${x(last.i)} ${y(Math.max(0, sc.min))}L${x(pts[0].i)} ${y(Math.max(0, sc.min))}Z`} fill={C.profit} opacity="0.08" />
        <path d={path} fill="none" stroke={C.profit} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {hp && <line x1={x(hp.i)} x2={x(hp.i)} y1={padT} y2={h - padB} stroke={C.text} strokeOpacity="0.2" />}
        {hp && <circle cx={x(hp.i)} cy={y(hp.v)} r="4.5" fill={C.profit} stroke={C.surface} strokeWidth="2" />}
        <circle cx={x(last.i)} cy={y(last.v)} r="4.5" fill={C.profit} stroke={C.surface} strokeWidth="2" />
        <text x={x(last.i)} y={y(last.v) - 11} textAnchor="end" fontSize="12.5" fontWeight="600" fill={C.text}>{last.v.toFixed(0)}%</text>
        {[0, Math.floor((rows.length - 1) / 2), rows.length - 1].map((i, k) => (
          <text key={k} x={x(i)} y={h - 8} textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"} fontSize="11.5" fill={C.axis}>{monthShort(rows[i].month)} {rows[i].month.slice(2, 4)}</text>
        ))}
      </svg>
      {hp && <Tip left={Math.min(Math.max(x(hp.i) + 12, 8), w - 160)} width={150}><div className="font-semibold text-ink-text">{monthLong(hp.m)}</div><TipRow color={C.profit} label="Margin" value={`${hp.v.toFixed(1)}%`} /></Tip>}
    </div>
  );
}

/* ------------------------------------------------------------------ weekly sales and the straight-line outlook */

export function ForecastChart({ weeks, projection, cur }: { weeks: AWeek[]; projection: AProjection[]; cur: string }) {
  const [ref, w] = useWidth(260);
  const [hover, setHover] = useState<number | null>(null);
  const n = weeks.length, m = projection.length, total = n + m;
  const h = 250, padL = 50, padR = 14, padT = 14, padB = 28;
  const sc = niceScale(0, Math.max(1, ...weeks.map((k) => k.sales), ...projection.map((p) => p.high)));
  const x = (i: number) => padL + (total <= 1 ? 0 : (i * (w - padL - padR)) / (total - 1));
  const y = (v: number) => padT + (1 - (v - sc.min) / (sc.max - sc.min)) * (h - padT - padB);
  const actual = weeks.map((k, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(k.sales).toFixed(1)}`).join("");
  const mid = m ? `M${x(n - 1)} ${y(weeks[n - 1].sales)}` + projection.map((p, i) => `L${x(n + i).toFixed(1)} ${y(p.expected).toFixed(1)}`).join("") : "";
  const band = m ? projection.map((p, i) => `${i ? "L" : "M"}${x(n + i).toFixed(1)} ${y(p.high).toFixed(1)}`).join("")
    + [...projection].reverse().map((p, i) => `L${x(n + m - 1 - i).toFixed(1)} ${y(p.low).toFixed(1)}`).join("") + "Z" : "";
  const move = (e: React.PointerEvent<SVGSVGElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    setHover(Math.max(0, Math.min(total - 1, Math.round(((px - padL) / Math.max(1, w - padL - padR)) * (total - 1)))));
  };
  const hw = hover != null && hover < n ? weeks[hover] : null, hp = hover != null && hover >= n ? projection[hover - n] : null;
  return (
    <div ref={ref} className="relative min-w-0 overflow-hidden">
      <svg width={w} height={h} role="img" aria-label={`Weekly sales for the last ${n} complete weeks and a straight-line outlook for the next ${m}.`} onPointerMove={move} onPointerLeave={() => setHover(null)} className="block touch-pan-y">
        {sc.ticks.map((t) => (
          <g key={t}><line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} stroke={C.grid} /><text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="11.5" fill={C.axis}>{compact(t)}</text></g>
        ))}
        {m > 0 && <rect x={x(n - 1)} y={padT} width={x(total - 1) - x(n - 1)} height={h - padT - padB} fill={C.wash} />}
        {m > 0 && <path d={band} fill={C.sales} opacity="0.14" />}
        {m > 0 && <path d={mid} fill="none" stroke={C.sales} strokeWidth="2" strokeDasharray="5 4" strokeLinecap="round" />}
        <path d={actual} fill="none" stroke={C.sales} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {weeks.map((k, i) => <circle key={k.week_start} cx={x(i)} cy={y(k.sales)} r="4" fill={C.sales} stroke={C.surface} strokeWidth="2" />)}
        {m > 0 && <text x={x(n - 1) + 8} y={padT + 12} fontSize="11.5" fill={C.axis}>Outlook</text>}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={padT} y2={h - padB} stroke={C.text} strokeOpacity="0.2" />}
        {[0, Math.floor((n - 1) / 2), n - 1].map((i, k) => <text key={k} x={x(i)} y={h - 8} textAnchor={k === 0 ? "start" : "middle"} fontSize="11.5" fill={C.axis}>{dayShort(weeks[i].week_start)}</text>)}
      </svg>
      {hw && hover != null && <Tip left={Math.min(Math.max(x(hover) + 12, 8), w - 200)}><div className="font-semibold text-ink-text mb-1">Week from {dayShort(hw.week_start)}</div><TipRow color={C.sales} label="Sales" value={`${cur} ${fmtFull(hw.sales)}`} /><TipRow color={C.spending} label="Spending" value={`${cur} ${fmtFull(hw.spending)}`} /></Tip>}
      {hp && hover != null && <Tip left={Math.min(Math.max(x(hover) - 190, 8), w - 200)}><div className="font-semibold text-ink-text mb-1">Outlook, week from {dayShort(hp.week_start)}</div><TipRow label="Expected" value={`${cur} ${fmtFull(hp.expected)}`} /><TipRow label="Likely range" value={`${compact(hp.low)} to ${compact(hp.high)}`} /></Tip>}
      <Legend items={[{ color: C.sales, label: "Weekly sales", kind: "line" }, ...(m ? [{ color: C.sales, label: "Outlook and likely range (8 in 10 weeks)", kind: "band" as const }] : [])]} />
    </div>
  );
}

/* ------------------------------------------------------------------ weekday columns */

export function WeekdayChart({ rows, cur }: { rows: { day: string; average_sales: number; days: number }[]; cur: string }) {
  const [ref, w] = useWidth(240);
  const [hover, setHover] = useState<number | null>(null);
  const h = 230, padL = 44, padR = 8, padT = 22, padB = 28;
  const sc = niceScale(0, Math.max(1, ...rows.map((r) => r.average_sales)), 4);
  const y = (v: number) => padT + (1 - v / sc.max) * (h - padT - padB);
  const band = (w - padL - padR) / 7, bw = Math.min(24, band * 0.5);
  const vals = rows.map((r) => r.average_sales), hi = vals.indexOf(Math.max(...vals)), lo = vals.indexOf(Math.min(...vals));
  return (
    <div ref={ref} className="relative min-w-0 overflow-hidden">
      <svg width={w} height={h} role="img" aria-label={`Average sales on each weekday. Best: ${rows[hi]?.day}. Weakest: ${rows[lo]?.day}.`} onPointerLeave={() => setHover(null)} className="block">
        {sc.ticks.map((t) => <g key={t}><line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} stroke={C.grid} /><text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="11.5" fill={C.axis}>{compact(t)}</text></g>)}
        {rows.map((r, i) => {
          const cx = padL + band * (i + 0.5);
          return (
            <g key={r.day} onPointerEnter={() => setHover(i)} onPointerMove={() => setHover(i)}>
              <rect x={padL + band * i} y={padT} width={band} height={h - padT - padB} fill={hover === i ? C.wash : "transparent"} />
              <path d={column(cx - bw / 2, y(r.average_sales), y(0), bw)} fill={C.sales} />
              {(i === hi || i === lo) && <text x={cx} y={y(r.average_sales) - 6} textAnchor="middle" fontSize="11.5" fontWeight="600" fill={C.text}>{compact(r.average_sales)}</text>}
              <text x={cx} y={h - 8} textAnchor="middle" fontSize="11.5" fill={C.axis}>{r.day.slice(0, 3)}</text>
            </g>
          );
        })}
      </svg>
      {hover != null && <Tip left={Math.min(Math.max(padL + band * (hover + 0.5) + 14, 8), w - 180)} width={170}><div className="font-semibold text-ink-text mb-1">{rows[hover].day}s</div><TipRow color={C.sales} label="Average" value={`${cur} ${fmtFull(rows[hover].average_sales)}`} /><TipRow label="Days counted" value={String(rows[hover].days)} /></Tip>}
    </div>
  );
}

/* ------------------------------------------------------------------ calendar heat map */

export function CalendarHeatmap({ daily, lastDay, cur }: { daily: { date: string; sales: number; spending: number }[]; lastDay: string; cur: string }) {
  const [ref, w] = useWidth(300);
  const [hover, setHover] = useState<{ date: string; x: number; y: number } | null>(null);
  const map = useMemo(() => new Map(daily.map((d) => [d.date, d])), [daily]);
  const first = daily.length ? daily[0].date : lastDay;
  const { cols, cuts } = useMemo(() => {
    const end = new Date(lastDay + "T00:00:00");
    const startRaw = new Date(end); startRaw.setDate(end.getDate() - 181);
    const start = new Date(startRaw); start.setDate(startRaw.getDate() - ((startRaw.getDay() + 6) % 7));       // back to Monday
    const weeks: { date: string; row: number }[][] = [];
    for (const d = new Date(start); d <= end;) {
      const col: { date: string; row: number }[] = [];
      for (let r = 0; r < 7 && d <= end; r++, d.setDate(d.getDate() + 1)) col.push({ date: d.toLocaleDateString("en-CA"), row: r });
      weeks.push(col);
    }
    const pos = daily.map((d) => d.sales).filter((v) => v > 0).sort((a, b) => a - b);
    const q = (f: number) => pos[Math.min(pos.length - 1, Math.floor(pos.length * f))] ?? 0;
    return { cols: weeks, cuts: [q(0.2), q(0.4), q(0.6), q(0.8)] };
  }, [daily, lastDay]);
  const labelW = 30, gap = 3;
  const cell = Math.max(10, Math.min(20, Math.floor((w - labelW) / cols.length) - gap));
  const step = cell + gap, gridH = 7 * step, h = gridH + 22;
  const shade = (v: number) => (v <= 0 ? "#eef1ec" : HEAT[cuts.filter((c) => v > c).length]);
  const months: { x: number; label: string }[] = [];
  cols.forEach((col, i) => { const d = col.find((c) => c.row === 0) ?? col[0]; if (d.date.slice(8) <= "07" && (i === 0 || d.date.slice(5, 7) !== (cols[i - 1].find((c) => c.row === 0) ?? cols[i - 1][0]).date.slice(5, 7))) months.push({ x: labelW + i * step, label: monthShort(d.date) }); });
  const hd = hover ? map.get(hover.date) : null;
  return (
    <div ref={ref} className="relative min-w-0 overflow-hidden">
      <svg width={w} height={h} role="img" aria-label="Calendar of daily sales for the last 26 weeks, darker is more. Use the table view for the best days." onPointerLeave={() => setHover(null)} className="block">
        {months.map((m) => <text key={m.x} x={m.x} y={11} fontSize="11.5" fill={C.axis}>{m.label}</text>)}
        {["Mon", "Wed", "Fri"].map((d, k) => <text key={d} x={0} y={22 + (k * 2) * step + cell - 3} fontSize="11" fill={C.axis}>{d}</text>)}
        {cols.map((col, i) => col.map((c) => {
          const d = map.get(c.date), none = c.date < first;
          return <rect key={c.date} x={labelW + i * step} y={22 + c.row * step} width={cell} height={cell} rx="3" fill={none ? "transparent" : shade(d?.sales ?? 0)} stroke={none ? C.grid : "none"}
            onPointerEnter={() => setHover({ date: c.date, x: labelW + i * step, y: 22 + c.row * step })} />;
        }))}
      </svg>
      {hover && <Tip left={Math.min(Math.max(hover.x - 70, 4), w - 190)} top={Math.max(0, hover.y + cell + 6)} width={180}>
        <div className="font-semibold text-ink-text mb-1">{dayLong(hover.date)}</div>
        {hd ? <><TipRow color={C.sales} label="Sales" value={`${cur} ${fmtFull(hd.sales)}`} /><TipRow color={C.spending} label="Spending" value={`${cur} ${fmtFull(hd.spending)}`} /></> : <span className="text-ink-muted">{hover.date < first ? "Before your records begin" : "No records this day"}</span>}
      </Tip>}
      <div className="flex items-center gap-2 text-xs text-ink-muted mt-3">
        <span>Less</span>{["#eef1ec", ...HEAT].map((c) => <span key={c} className="inline-block w-3.5 h-3.5 rounded-[3px]" style={{ background: c }} />)}<span>More sales</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ small pieces */

export function Spark({ values, color = C.sales, width = 112, height = 30 }: { values: number[]; color?: string; width?: number; height?: number }) {
  if (values.length < 3) return <div style={{ width, height }} aria-hidden />;
  const mn = Math.min(...values), mx = Math.max(...values), span = mx - mn || 1;
  const pts = values.map((v, i) => [3 + (i * (width - 8)) / (values.length - 1), 4 + (1 - (v - mn) / span) * (height - 10)] as const);
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg width={width} height={height} aria-hidden="true" className="block">
      <path d={pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`).join("")} fill="none" stroke={C.muted} strokeWidth="1.75" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="3.5" fill={color} stroke={C.surface} strokeWidth="2" />
    </svg>
  );
}

/** A horizontal bar row: label, a thin bar, the value at its tip. */
export function HRow({ label, sub, value, max, text, color = C.sales, trailing }: { label: string; sub?: string; value: number; max: number; text: string; color?: string; trailing?: ReactNode }) {
  const pct = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <div className="grid grid-cols-[minmax(0,7.5rem)_1fr] sm:grid-cols-[minmax(0,12rem)_1fr_auto] items-center gap-x-3 gap-y-0.5 text-sm py-1.5">
      <div className="min-w-0"><div className="truncate text-ink-text" title={label}>{label}</div>{sub && <div className="text-xs text-ink-faint">{sub}</div>}</div>
      <div className="flex items-center gap-2 min-w-0">
        <div className="h-2.5 rounded-r-[4px] shrink-0" style={{ width: `${pct * 48}%`, minWidth: value > 0 ? 3 : 0, background: color }} />
        <span className="kpi-figure text-ink-text whitespace-nowrap">{text}</span>
      </div>
      {trailing != null && <div className="col-start-2 sm:col-start-auto text-left sm:text-right text-xs text-ink-muted whitespace-nowrap sm:min-w-[3.5rem]">{trailing}</div>}
    </div>
  );
}

/** A change against the period before: arrow, size and plain words, coloured good or bad by whether up is good. Never colour alone. */
export function Change({ pct, goodWhen, suffix = "" }: { pct: number | null | undefined; goodWhen: "up" | "down"; suffix?: string }) {
  if (pct == null) return <span className="text-ink-faint">new</span>;
  if (Math.abs(pct) < 1) return <span className="text-ink-muted">flat</span>;
  const good = (pct > 0) === (goodWhen === "up");
  return <span className="kpi-figure" style={{ color: good ? C.good : C.bad }}>{pct > 0 ? "▲" : "▼"} {Math.abs(pct) >= 1000 ? `${(Math.abs(pct) / 100).toFixed(0)}x` : `${Math.abs(pct).toFixed(0)}%`}{suffix}</span>;
}

export function DataTable({ head, rows, numeric = [] }: { head: string[]; rows: (string | number)[][]; numeric?: number[] }) {
  return (
    <div className="overflow-x-auto -mx-1">
      <table className="w-full text-sm">
        <thead><tr className="text-left text-xs text-ink-muted border-b border-border">{head.map((c, i) => <th key={c} scope="col" className={`py-2 px-2 font-medium ${numeric.includes(i) ? "text-right" : ""}`}>{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, ri) => <tr key={ri} className="border-b border-border/60 last:border-0">{r.map((c, i) => <td key={i} className={`py-1.5 px-2 ${numeric.includes(i) ? "text-right kpi-figure" : ""}`}>{typeof c === "number" ? fmtFull(c) : c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

/** A chart panel: title, a one-line reading of it, and a switch to the same figures as a table. */
export function ChartCard({ title, sub, children, table, className = "" }: { title: string; sub?: string; children: ReactNode; table?: { head: string[]; rows: (string | number)[][]; numeric?: number[] }; className?: string }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <section className={`bg-panel border border-border rounded-xl p-5 break-inside-avoid min-w-0 ${className}`}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold tracking-tight">{title}</h3>
          {sub && <p className="text-sm text-ink-muted mt-0.5 max-w-prose">{sub}</p>}
        </div>
        {table && (
          <div role="group" aria-label={`${title}: view`} className="inline-flex border border-field rounded-sm overflow-hidden shrink-0 text-xs print:hidden">
            {(["chart", "table"] as const).map((v) => <button key={v} aria-pressed={view === v} onClick={() => setView(v)} className={`px-3 py-2 lg:px-2.5 lg:py-1 font-medium ${view === v ? "bg-info text-white" : "bg-panel text-ink-text hover:bg-panel-raised"}`}>{v === "chart" ? "Chart" : "Table"}</button>)}
          </div>
        )}
      </div>
      {view === "chart" || !table ? children : <DataTable {...table} />}
    </section>
  );
}
