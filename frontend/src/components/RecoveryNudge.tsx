import { useState } from "react";
import { useRecoveryMissing } from "../lib/useRecoveryMissing";

let dismissed = false;     // lasts until the page is reloaded

/** A one-line reminder on the Settings page, shown until the person has set security questions. Elsewhere a small dot on
 *  the Settings link says the same thing without taking room from the figures. */
export function RecoveryNudge() {
  const missing = useRecoveryMissing();
  const [hidden, setHidden] = useState(dismissed);
  if (!missing || hidden) return null;
  return (
    <p role="status" className="text-sm border border-border bg-panel rounded-sm px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1">
      <span>Set your security questions so you can get back in if you forget your password.</span>
      <a href="#sq" className="text-info hover:underline font-medium py-2 lg:py-0">Set them now</a>
      <button type="button" onClick={() => { dismissed = true; setHidden(true); }} className="ml-auto text-xs text-ink-muted hover:text-ink-text py-2 lg:py-0">Not now</button>
    </p>
  );
}
