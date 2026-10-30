import { useEffect, useSyncExternalStore } from "react";
import { securityQuestionsApi } from "../api/client";
import { cache } from "../api/cache";

const listeners = new Set<() => void>();
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const notify = () => listeners.forEach((l) => l());
let inflight: Promise<void> | null = null;

/** Asks the server once per sign-in (however many places want to know) whether security questions are set. A failed ask
 *  leaves the answer unknown, which counts as "not missing", so nobody is nagged because of a bad connection. */
function ask(): Promise<void> {
  inflight ??= securityQuestionsApi.get()
    .then((r) => { cache.recoverySet = r.questions.length > 0; notify(); })
    .catch(() => undefined)
    .finally(() => { inflight = null; });
  return inflight;
}

/** The person has just saved their security questions: every place that showed the reminder clears at once. */
export function markRecoverySet() { cache.recoverySet = true; notify(); }

/** True while the account has no way back in without email. */
export function useRecoveryMissing(): boolean {
  const missing = useSyncExternalStore(subscribe, () => cache.recoverySet === false);
  useEffect(() => { if (cache.recoverySet === null) void ask(); }, []);
  return missing;
}
