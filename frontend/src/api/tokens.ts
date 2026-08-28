/**
 * Token storage.
 *
 * localStorage is a deliberate MVP trade-off: it is readable by any script on the
 * origin, so it trades XSS resistance for a simple, stateless client. The stronger
 * option — refresh token in an httpOnly cookie — needs CSRF protection and a
 * same-site deployment story, which is a Milestone 6 hardening item, not an M1 one.
 */

const ACCESS_KEY = "wpx.access_token";
const REFRESH_KEY = "wpx.refresh_token";

export interface StoredTokens {
  accessToken: string;
  refreshToken: string;
}

export function readTokens(): StoredTokens | null {
  try {
    const accessToken = localStorage.getItem(ACCESS_KEY);
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    if (!accessToken || !refreshToken) return null;
    return { accessToken, refreshToken };
  } catch {
    return null;
  }
}

export function writeTokens(tokens: StoredTokens): void {
  try {
    localStorage.setItem(ACCESS_KEY, tokens.accessToken);
    localStorage.setItem(REFRESH_KEY, tokens.refreshToken);
  } catch {
    /* private browsing — the session simply won't survive a reload */
  }
}

export function clearTokens(): void {
  try {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* nothing to clear */
  }
}
