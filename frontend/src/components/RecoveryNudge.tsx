import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { securityQuestionsApi } from "../api/client";
import { cache } from "../api/cache";

let dismissed = false;     // lasts until the page is reloaded

/** A one-line reminder, shown until the person has set security questions. */
export function RecoveryNudge() {
  const [missing, setMissing] = useState(cache.recoverySet === false);
  const [hidden, setHidden] = useState(dismissed);

  useEffect(() => {
    if (cache.recoverySet !== null) return;
    securityQuestionsApi.get().then((r) => { cache.recoverySet = r.questions.length > 0; setMissing(!cache.recoverySet); }).catch(() => {});
  }, []);

  if (!missing || hidden || cache.recoverySet) return null;
  return (
    <p role="status" className="text-sm border border-border bg-panel rounded-sm px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1">
      <span>Set your security questions so you can get back in if you forget your password.</span>
      <Link to="/settings#sq" className="text-info hover:underline font-medium">Set them now</Link>
      <button type="button" onClick={() => { dismissed = true; setHidden(true); }} className="ml-auto text-xs text-ink-muted hover:text-ink-text">Not now</button>
    </p>
  );
}
