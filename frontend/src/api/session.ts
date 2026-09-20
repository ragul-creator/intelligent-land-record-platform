const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface SessionTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  access_expires_in_seconds: number;
  refresh_expires_in_seconds: number;
}

let refreshPromise: Promise<boolean> | null = null;

function notifySessionExpired() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event("session-expired"));
}

export function storeSessionTokens(tokens: SessionTokens): void {
  const now = Date.now();
  sessionStorage.setItem("access_token", tokens.access_token);
  sessionStorage.setItem("refresh_token", tokens.refresh_token);
  sessionStorage.setItem("access_expires_at", String(now + tokens.access_expires_in_seconds * 1000));
  sessionStorage.setItem("refresh_expires_at", String(now + tokens.refresh_expires_in_seconds * 1000));
}

export function clearSessionTokens(notify = false): void {
  sessionStorage.removeItem("access_token");
  sessionStorage.removeItem("refresh_token");
  sessionStorage.removeItem("access_expires_at");
  sessionStorage.removeItem("refresh_expires_at");
  if (notify) notifySessionExpired();
}

export function hasStoredSession(): boolean {
  return Boolean(sessionStorage.getItem("access_token") && sessionStorage.getItem("refresh_token"));
}

function accessNeedsRefresh(): boolean {
  const expiresAt = Number(sessionStorage.getItem("access_expires_at") ?? "0");
  return expiresAt > 0 && Date.now() >= expiresAt - 15_000;
}

async function rotateRefreshToken(): Promise<boolean> {
  const refreshToken = sessionStorage.getItem("refresh_token");
  if (!refreshToken) return false;

  const refreshExpiresAt = Number(sessionStorage.getItem("refresh_expires_at") ?? "0");
  if (refreshExpiresAt > 0 && Date.now() >= refreshExpiresAt) {
    clearSessionTokens(true);
    return false;
  }

  try {
    const response = await fetch(`${baseUrl}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!response.ok) {
      clearSessionTokens(true);
      return false;
    }
    storeSessionTokens(await response.json() as SessionTokens);
    return true;
  } catch {
    return false;
  }
}

export async function refreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = rotateRefreshToken().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  const token = sessionStorage.getItem("access_token");
  return fetch(`${baseUrl}/api/v1${path}`, {
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
}

export async function sessionFetch(path: string, init?: RequestInit): Promise<Response> {
  if (hasStoredSession() && accessNeedsRefresh()) await refreshSession();
  let response = await send(path, init);
  if (response.status === 401 && sessionStorage.getItem("refresh_token")) {
    const refreshed = await refreshSession();
    if (refreshed) response = await send(path, init);
  }
  return response;
}

export function apiBaseUrl(): string {
  return baseUrl;
}
