import type { Inspection } from "../api/client";

const GUESS_LABEL: Record<string, string> = { timestamp_col: "the date column", amount_col: "the amount column" };
const MONEY = ["amount_col", "amount_in_col", "amount_out_col", "price_col"];
const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

interface Line { ok: boolean; text: string }

/** "What we found": what the file held, what was set aside, and what needs the owner's eye. The point is that nothing is taken
 *  blindly: every empty row, nameless column, unreadable cell and guess is said out loud before anything is counted. */
export function FoundPanel({ info, mapping }: { info: Inspection; mapping: Record<string, string | undefined> }) {
  const q = info.quality;
  const hasMoney = MONEY.some((k) => mapping[k]);
  const hasDate = Boolean(mapping.timestamp_col);
  const lines: Line[] = [{ ok: true, text: `Read ${plural(info.row_count, "row")} and ${plural(info.columns.length, "column")}.` }];
  if (q) {
    if (q.blank_rows) lines.push({ ok: true, text: `Ignored ${plural(q.blank_rows, "empty row")}.` });
    if (q.blank_columns.length) lines.push({ ok: true, text: `Ignored ${plural(q.blank_columns.length, "empty column")}.` });
    if (q.unnamed_columns.length) lines.push({ ok: false, text: `${plural(q.unnamed_columns.length, "column")} ${q.unnamed_columns.length === 1 ? "has" : "have"} data but no name, so ${q.unnamed_columns.length === 1 ? "it is" : "they are"} shown as “(no name)”. ${q.unnamed_columns.length === 1 ? "It is" : "They are"} not used unless you pick ${q.unnamed_columns.length === 1 ? "it" : "them"} below.` });
    if (q.unreadable_dates) lines.push({ ok: false, text: `${plural(q.unreadable_dates, "row")} ${q.unreadable_dates === 1 ? "has" : "have"} a date we can't read. ${q.unreadable_dates === 1 ? "It" : "They"} will be skipped and listed after the import.` });
    if (q.unreadable_amounts) lines.push({ ok: false, text: `${plural(q.unreadable_amounts, "amount")} can't be read as a number. Those rows will be skipped and listed after the import.` });
    if (hasMoney && q.rows_without_amount) lines.push({ ok: false, text: `${plural(q.rows_without_amount, "row")} ${q.rows_without_amount === 1 ? "has" : "have"} no amount. ${q.rows_without_amount === 1 ? "It is" : "They are"} kept, but add nothing to your totals.` });
  }
  for (const g of info.guessed ?? []) lines.push({ ok: false, text: `We guessed ${GUESS_LABEL[g] ?? g} from what is in it, not from its name. Please check it below.` });
  if (!hasDate && hasMoney) lines.push({ ok: false, text: "There is no date column. Below, say when these happened." });
  if (hasDate && !hasMoney) lines.push({ ok: false, text: "No amount column was found. Without one we can count records, but not money. If an amount is there under another name, pick it below." });
  if (!hasDate && !hasMoney) lines.push({ ok: false, text: "This looks like a list (products or stock, for example), not sales or expenses. This app looks at money coming in and going out over time. If the file has amounts, pick them below; if not, it is not the right thing to connect." });

  const cols = info.columns.slice(0, 8);
  return (
    <div className="space-y-3" aria-label="What we found">
      <div className="font-semibold">What we found</div>
      <ul className="space-y-1.5 text-sm">
        {lines.map((l, i) => (
          <li key={i} className="flex gap-2"><span aria-hidden className="shrink-0 font-semibold" style={{ color: l.ok ? "#1b8a5a" : "#b8860b" }}>{l.ok ? "✓" : "!"}</span><span className={l.ok ? "text-ink-muted" : ""}>{l.text}</span></li>
        ))}
      </ul>
      <div className="overflow-x-auto border border-border rounded-sm">
        <table className="text-xs w-full">
          <thead className="bg-panel-raised"><tr>{cols.map((c) => <th key={c} className="text-left font-medium px-2.5 py-1.5 whitespace-nowrap">{/^Unnamed: \d+$/.test(c) ? "(no name)" : c}</th>)}</tr></thead>
          <tbody>{info.sample.slice(0, 3).map((r, i) => <tr key={i} className="border-t border-border">{cols.map((c) => <td key={c} className="px-2.5 py-1.5 whitespace-nowrap max-w-[14rem] truncate">{String(r[c] ?? "")}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {info.columns.length > 8 && <div className="text-xs text-ink-faint">Showing the first 8 of {info.columns.length} columns. The next step lets you choose from all of them.</div>}
    </div>
  );
}
