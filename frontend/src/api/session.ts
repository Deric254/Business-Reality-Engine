// Token storage for the signed-in user. sessionStorage (tab-scoped, cleared on
// tab close) rather than localStorage: a shorter exposure window if a token
// is ever read by injected script. Every access is wrapped — storage can throw
// (private mode, blocked site data) and the app must still render.
const ACCESS_KEY = "bre.access";
const REFRESH_KEY = "bre.refresh";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

let memoryTokens: TokenPair | null = null;
const listeners = new Set<() => void>();

function read(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* fall back to in-memory only */
  }
}

export function getTokens(): TokenPair | null {
  if (memoryTokens) return memoryTokens;
  const access = read(ACCESS_KEY);
  const refresh = read(REFRESH_KEY);
  return access && refresh ? { access_token: access, refresh_token: refresh } : null;
}

export function setTokens(tokens: TokenPair | null) {
  memoryTokens = tokens;
  write(ACCESS_KEY, tokens?.access_token ?? null);
  write(REFRESH_KEY, tokens?.refresh_token ?? null);
  listeners.forEach((fn) => fn());
}

export function onSessionChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function isSignedIn(): boolean {
  return getTokens() !== null;
}
