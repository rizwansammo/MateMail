/**
 * Client-side token handling.
 *
 * The refresh token is deliberately absent from this file. Since P3c it lives
 * in an HttpOnly cookie the browser attaches to `/api/auth/` on its own, and
 * no code here can read or write it — that is the point. If you are looking
 * for a `getRefreshToken`, there isn't one, and adding one would undo the
 * change.
 *
 * The access token is still readable by JavaScript. That residual exposure is
 * documented rather than hidden: an XSS can act as the user until the access
 * token expires (15 minutes by default) but cannot mint a new one, because the
 * refresh token is out of reach. `sessionStorage` also scopes it to the tab
 * and clears it when the tab closes.
 */
const ACCESS_KEY = "mm_access";

/** Key used before P3c. Read once, to clean up, then never again. */
const LEGACY_REFRESH_KEY = "mm_refresh";

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem(ACCESS_KEY);
}

export function setAccessToken(access: string): void {
  sessionStorage.setItem(ACCESS_KEY, access);
}

export function clearTokens(): void {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(ACCESS_KEY);
  purgeLegacyRefreshToken();
}

/**
 * Remove any refresh token left in localStorage by a pre-P3c session.
 *
 * Browsers that used the old build still hold a valid refresh token in
 * localStorage. It is a live credential for up to seven days, and shipping the
 * fix without clearing it would leave exactly the exposure this change exists
 * to remove — just no longer written to by anything.
 */
export function purgeLegacyRefreshToken(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(LEGACY_REFRESH_KEY);
  } catch {
    // Storage can be unavailable (private mode, blocked site data). Nothing
    // to clean up in that case.
  }
}

export function isTokenExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.exp * 1000 < Date.now();
  } catch {
    return true;
  }
}

export function getTokenPayload(token: string): Record<string, unknown> | null {
  try {
    return JSON.parse(atob(token.split(".")[1]));
  } catch {
    return null;
  }
}
