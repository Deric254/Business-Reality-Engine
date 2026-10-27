// Token storage for the signed-in user. By default sessionStorage (tab-scoped, cleared on tab close): a shorter
// exposure window if a token is ever read by injected script. A person who ticks "Keep me signed in on this device" at
// sign-in is kept in localStorage instead, so the app opens signed in next time; that choice is theirs alone, it is
// remembered for as long as the tokens are (a refresh keeps it), and signing out clears both. Every access is wrapped:
// storage can throw (private mode, blocked site data) and the app must still render.
const ACCESS_KEY = "bre.access";
const REFRESH_KEY = "bre.refresh";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

let memoryTokens: TokenPair | null = null;
const listeners = new Set<() => void>();

type Where = "session" | "device";

function store(where: Where): Storage {
  return where === "device" ? localStorage : sessionStorage;
}

function read(where: Where, key: string): string | null {
  try {
    return store(where).getItem(key);
  } catch {
    return null;
  }
}

function write(where: Where, key: string, value: string | null) {
  try {
    if (value === null) store(where).removeItem(key);
    else store(where).setItem(key, value);
  } catch {
    /* fall back to in-memory only */
  }
}

function saved(where: Where): TokenPair | null {
  const access = read(where, ACCESS_KEY);
  const refresh = read(where, REFRESH_KEY);
  return access && refresh ? { access_token: access, refresh_token: refresh } : null;
}

/** True when this device was told to stay signed in. */
export function isRemembered(): boolean {
  return saved("device") !== null;
}

export function getTokens(): TokenPair | null {
  return memoryTokens ?? saved("session") ?? saved("device");
}

/** `remember` is only given at sign-in. Later calls (a refresh, a password change) keep whatever the person chose. */
export function setTokens(tokens: TokenPair | null, remember?: boolean) {
  const where: Where = tokens && (remember ?? isRemembered()) ? "device" : "session";
  const other: Where = where === "device" ? "session" : "device";
  memoryTokens = tokens;
  write(where, ACCESS_KEY, tokens?.access_token ?? null);
  write(where, REFRESH_KEY, tokens?.refresh_token ?? null);
  write(other, ACCESS_KEY, null);
  write(other, REFRESH_KEY, null);
  listeners.forEach((fn) => fn());
}

export function onSessionChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function isSignedIn(): boolean {
  return getTokens() !== null;
}

// Display-only view of the access token's claims (the server verifies; this just decides what to show).
export interface Claims {
  role: string | null;
  mustChangePassword: boolean;
}

export function getClaims(): Claims {
  const token = getTokens()?.access_token;
  if (!token) return { role: null, mustChangePassword: false };
  try {
    const b64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(b64.padEnd(Math.ceil(b64.length / 4) * 4, "=")));
    return { role: typeof payload.role === "string" ? payload.role : null, mustChangePassword: payload.mcp === true };
  } catch {
    return { role: null, mustChangePassword: false };
  }
}
