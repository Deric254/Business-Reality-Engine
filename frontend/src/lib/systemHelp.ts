import type { SystemLink } from "../api/client";

export type Tone = "good" | "wait" | "bad";
export const TONE_COLOR: Record<Tone, string> = { good: "#1b8a5a", wait: "#d4a017", bad: "#c0392b" };
export interface Plain { tone: Tone; title: string; todo: string }

/** What a system's link is doing, in words a shop owner can act on. The server's own wording is technical ("needs a one-time
 *  reader installed"); this says what happened and what to do next. Every state the server can report is covered. */
export function describeSystem(l: Pick<SystemLink, "label" | "state" | "connected">): Plain {
  const name = l.label;
  if (!l.connected) return { tone: "wait", title: "Not connected yet", todo: `Connect ${name} to start.` };
  switch (l.state) {
    case "connected": return { tone: "good", title: "Connected", todo: "Nothing to do. It keeps itself up to date." };
    case "waiting": return { tone: "wait", title: "Waiting for the helper app", todo: `On the computer that has ${name}, open the helper app and paste your code.` };
    case "offline": return { tone: "bad", title: "The helper app has gone quiet", todo: "Is the computer switched on? Open the helper app again. It starts by itself when the computer starts." };
    case "not_found": return { tone: "bad", title: `Couldn't find ${name} on that computer`, todo: `Is ${name} installed there? If it is on a different computer, open the helper app on that one. If you know where its data file is, use Manage, then Set location.` };
    case "not_this_system": return { tone: "bad", title: `Found a file, but it isn't ${name}'s`, todo: "Use Manage, then Set location, and point it to the right file." };
    case "needs_driver": return { tone: "bad", title: "One extra step on that computer", todo: `${name}'s data is protected, and the helper app needs a small reader to open it. Ask your support person to install it, then press Check again.` };
    case "needs_key": return { tone: "bad", title: `${name}'s key file is missing`, todo: "The helper app needs the system's key file next to its data file. Ask your support person, then press Check again." };
    case "locked": return { tone: "wait", title: `${name} is busy`, todo: "Nothing to do. We are trying again by ourselves." };
    case "unreadable": return { tone: "bad", title: `Couldn't read ${name}'s data`, todo: "Press Check again. If it keeps happening, show your support person the details below." };
    case "schema_changed": return { tone: "bad", title: `${name} has been updated`, todo: "Your figures are safe and nothing was changed. The connection needs a small update; ask your support person." };
    case "needs_choice": return { tone: "wait", title: "Which business should we read?", todo: "This computer holds more than one business. Choose yours below." };
    case "needs_confirmation": return { tone: "wait", title: "A big change is waiting for your OK", todo: "The data changed a lot at once. If that is expected, press Confirm." };
    case "refused": return { tone: "bad", title: "This connection was turned off", todo: "Press Get a new code, and paste it into the helper app." };
    default: return { tone: "wait", title: "Checking…", todo: "One moment." };
  }
}

/** The one line that tells an owner whether everything is fine, for the top of the page. */
export function overallHealth(systems: Pick<SystemLink, "label" | "state" | "connected" | "paused">[], sourcesFailing: number, sourceCount: number):
  { tone: Tone; text: string } | null {
  const connected = systems.filter((s) => s.connected);
  const total = connected.length + sourceCount;
  if (total === 0) return null;
  const bad = connected.filter((s) => !s.paused && describeSystem(s).tone === "bad").length + sourcesFailing;
  const waiting = connected.filter((s) => !s.paused && describeSystem(s).tone === "wait").length;
  if (bad > 0) return { tone: "bad", text: `${bad} of your ${total} connection${total === 1 ? "" : "s"} need${bad === 1 ? "s" : ""} attention.` };
  if (waiting > 0) return { tone: "wait", text: `${waiting} connection${waiting === 1 ? " is" : "s are"} still being set up.` };
  return { tone: "good", text: total === 1 ? "Your connection is working." : `All ${total} of your connections are working.` };
}
