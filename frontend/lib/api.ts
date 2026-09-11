import { clearTokens, getAccessToken, isTokenExpired, setAccessToken } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

let _refreshPromise: Promise<string | null> | null = null;

/**
 * Exchange the refresh cookie for a new access token.
 *
 * There is no request body and no token read from storage: the browser
 * attaches the HttpOnly cookie to `/api/auth/` itself, and `credentials:
 * "include"` is what tells fetch to send it. The rotated refresh token comes
 * back as a replacement cookie the same way — nothing here ever sees it.
 */
async function refreshAccessToken(): Promise<string | null> {
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/refresh/`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        clearTokens();
        return null;
      }
      const data = await res.json();
      setAccessToken(data.access);
      return data.access;
    } catch {
      clearTokens();
      return null;
    } finally {
      _refreshPromise = null;
    }
  })();
  return _refreshPromise;
}

async function getValidAccessToken(): Promise<string | null> {
  let access = getAccessToken();
  if (access && !isTokenExpired(access)) return access;
  access = await refreshAccessToken();
  return access;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getValidAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  // `credentials: "include"` is what carries the HttpOnly refresh cookie.
  // fetch defaults to "same-origin", which is enough in production (the API and
  // the app share app.matemail.online) but not in development, where the app
  // runs on :3000 and the API on :8000. The cookie's own Path limits it to
  // /api/auth/, so this does not attach a credential to anything else.
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...options,
    headers,
  });

  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, body || res.statusText);
  }

  const text = await res.text();
  return text ? JSON.parse(text) : ({} as T);
}

/**
 * Raw fetch wrapper — attaches Bearer token and returns the Response directly.
 * Callers check res.ok themselves. Use `api.*` helpers when you want auto-parse + throw on error.
 */
export async function apiRequest(path: string, options: RequestInit = {}): Promise<Response> {
  const token = await getValidAccessToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body && !(options.headers as Record<string, string>)?.["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  return fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...options,
    headers,
  });
}

export const api = {
  get: <T>(path: string, opts?: RequestInit) =>
    request<T>(path, { method: "GET", ...opts }),
  post: <T>(path: string, body?: unknown, opts?: RequestInit) =>
    request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
      ...opts,
    }),
  patch: <T>(path: string, body?: unknown, opts?: RequestInit) =>
    request<T>(path, {
      method: "PATCH",
      body: body ? JSON.stringify(body) : undefined,
      ...opts,
    }),
  delete: <T>(path: string, opts?: RequestInit) =>
    request<T>(path, { method: "DELETE", ...opts }),
};
