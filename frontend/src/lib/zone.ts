// The server stores delivery times (quiet hours, digest hour) in UTC; owners think in their own clock.
// These convert between the two using the business's time zone, so the screen never asks anyone to do sums.

/** Minutes the zone is ahead of UTC right now (Nairobi: 180). */
export function zoneOffsetMinutes(zone: string, at = new Date()): number {
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).formatToParts(at).map((p) => [p.type, p.value]));
    const asIfUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute);
    return Math.round((asIfUtc - Math.floor(at.getTime() / 60000) * 60000) / 60000);
  } catch {
    return 0;       // unknown zone name: treat as UTC rather than fail
  }
}

const DAY = 1440;

/** "HH:MM" moved by a number of minutes, wrapping around midnight. */
export function shiftTime(hhmm: string, minutes: number): string {
  if (!/^\d{2}:\d{2}/.test(hhmm)) return hhmm;
  const total = (((+hhmm.slice(0, 2) * 60 + +hhmm.slice(3, 5) + minutes) % DAY) + DAY) % DAY;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export const utcToLocal = (hhmm: string, zone: string) => shiftTime(hhmm, zoneOffsetMinutes(zone));
export const localToUtc = (hhmm: string, zone: string) => shiftTime(hhmm, -zoneOffsetMinutes(zone));
export const utcHourToLocal = (h: number, zone: string) => +shiftTime(`${String(h).padStart(2, "0")}:00`, zoneOffsetMinutes(zone)).slice(0, 2);
export const localHourToUtc = (h: number, zone: string) => +shiftTime(`${String(h).padStart(2, "0")}:00`, -zoneOffsetMinutes(zone)).slice(0, 2);
