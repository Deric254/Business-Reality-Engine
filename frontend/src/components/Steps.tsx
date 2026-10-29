import { useState, type ReactNode } from "react";
import { btnCls } from "./ui";

const GOOD = "#1b8a5a";

/** A numbered step. When done it shows a tick, so the person always sees where they are. */
export function Step({ n, title, done, children }: { n: number; title: string; done?: boolean; children?: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden className="shrink-0 w-7 h-7 rounded-full grid place-items-center text-sm font-semibold text-white bg-info" style={done ? { background: GOOD } : undefined}>{done ? "✓" : n}</span>
      <div className="space-y-2 min-w-0 flex-1">
        <div className="font-semibold">{title}{done && <span className="sr-only"> (done)</span>}</div>
        {children}
      </div>
    </li>
  );
}

/** A value to copy (a link, a code) with a button that says it worked. */
export function CopyBox({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try { await navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 2500); } catch { setCopied(false); }
  }
  return (
    <div className="space-y-2">
      <code className="block text-xs break-all bg-panel border border-field rounded-sm p-2.5 select-all" aria-label={label}>{value}</code>
      <button type="button" className={btnCls} onClick={copy}>{copied ? "Copied ✓" : `Copy ${label}`}</button>
    </div>
  );
}

/** A coloured dot and a sentence that says what is happening right now. */
export function Live({ tone, title, children }: { tone: "good" | "wait" | "bad"; title: string; children?: ReactNode }) {
  const color = tone === "good" ? GOOD : tone === "bad" ? "#c0392b" : "#d4a017";
  return (
    <div className="border rounded-xl p-3.5 space-y-1" style={{ borderColor: color }} role="status">
      <div className="font-semibold flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full" style={{ background: color }} aria-hidden />{title}</div>
      {children && <div className="text-sm text-ink-muted space-y-2">{children}</div>}
    </div>
  );
}
