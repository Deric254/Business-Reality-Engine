/** True on a phone or tablet running Android or iOS/iPadOS, where a Windows program cannot be run. Windows tablets are not
 *  included: they can run it. Used only to explain things, never to block them. */
export function isPhoneOrTablet(nav: Pick<Navigator, "userAgent" | "maxTouchPoints"> | undefined = typeof navigator === "undefined" ? undefined : navigator): boolean {
  if (!nav) return false;
  if (/Android|iPhone|iPad|iPod/i.test(nav.userAgent)) return true;
  return /Macintosh/i.test(nav.userAgent) && nav.maxTouchPoints > 1;        // iPadOS reports itself as a Mac
}
