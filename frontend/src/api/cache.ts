import type { Tenant, CoverageRow } from "./client";
import { getTokens, onSessionChange } from "./session";

// Last-seen values, shown instantly on navigation while a fresh copy loads in the background.
// Emptied when the session ends so one person's data is never shown to the next.
export const cache: { tenants: Tenant[] | null; coverage: Map<string, CoverageRow[]> } = {
  tenants: null,
  coverage: new Map(),
};

onSessionChange(() => {
  if (!getTokens()) {
    cache.tenants = null;
    cache.coverage.clear();
  }
});
