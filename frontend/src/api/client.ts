import { ApiError, type ApiErrorBody } from "./types";
import { clearTokens, readTokens, writeTokens } from "./tokens";

const BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000/api/v1";

type Method = "GET" | "POST" | "PUT" | "DELETE";

interface RequestOptions {
  method?: Method;
  body?: unknown;
  auth?: boolean;
  /** Internal: stops a refresh failure from recursing. */
  retryOnUnauthorized?: boolean;
}

/** Callback the auth context registers so a dead session logs the user out. */
let onSessionExpired: (() => void) | null = null;

export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody = {
    code: "unexpected_error",
    message: `Request failed with status ${response.status}.`,
    fields: null,
  };
  try {
    const parsed = (await response.json()) as { error?: ApiErrorBody };
    if (parsed.error) body = parsed.error;
  } catch {
    /* non-JSON response — keep the generic message */
  }
  return new ApiError(response.status, body);
}

/**
 * A refresh in flight, shared by every caller that needs one.
 *
 * This must be single-flight. Refresh tokens rotate on use, and presenting an
 * already-rotated token is treated server-side as theft — it revokes the whole
 * token family. A page that issues two authenticated requests at once (the
 * dashboard does) would otherwise fire two refreshes with the same token: the
 * first rotates it, the second looks like a replay, and the user is signed out.
 */
let inFlightRefresh: Promise<boolean> | null = null;

async function performRefresh(): Promise<boolean> {
  const stored = readTokens();
  if (!stored) return false;

  try {
    const response = await fetch(`${BASE_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: stored.refreshToken }),
    });
    if (!response.ok) return false;

    const tokens = (await response.json()) as {
      access_token: string;
      refresh_token: string;
    };
    writeTokens({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
    return true;
  } catch {
    return false;
  }
}

/**
 * One refresh attempt per failed request, then give up. Concurrent callers join
 * the refresh already running rather than starting their own.
 */
function refreshSession(): Promise<boolean> {
  inFlightRefresh ??= performRefresh().finally(() => {
    inFlightRefresh = null;
  });
  return inFlightRefresh;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true, retryOnUnauthorized = true } = options;

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (auth) {
    const stored = readTokens();
    if (stored) headers.Authorization = `Bearer ${stored.accessToken}`;
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401 && auth && retryOnUnauthorized) {
    if (await refreshSession()) {
      return request<T>(path, { ...options, retryOnUnauthorized: false });
    }
    clearTokens();
    onSessionExpired?.();
    throw await parseError(response);
  }

  if (!response.ok) {
    throw await parseError(response);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown, auth = true) =>
    request<T>(path, { method: "POST", body, auth }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
