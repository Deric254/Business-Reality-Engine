import type { Inspection } from "../api/client";

const UNNAMED = /^Unnamed: \d+$/;

/** A column's name as a person should see it. A header left blank in the sheet arrives as "Unnamed: 2", which means nothing
 *  to an owner, so say what it is: no name, and what its first cell holds. The real name is still what gets sent back. */
export function columnLabel(name: string, info?: Pick<Inspection, "sample"> | null): string {
  if (!UNNAMED.test(name)) return name;
  const first = (info?.sample ?? []).map((r) => r[name]).find((v) => v != null && String(v).trim() !== "");
  return first != null ? `(no name) for example “${String(first).slice(0, 24)}”` : "(no name)";
}

export const todayISO = () => new Date().toISOString().slice(0, 10);
