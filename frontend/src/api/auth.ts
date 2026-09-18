import { ApiError, type CurrentUser } from "./gis";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface ProjectSummary {
  id: string;
  name: string;
  description: string | null;
  state: "ACTIVE" | "ARCHIVED";
  owner_id: string;
  created_at: string;
  updated_at: string;
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  access_expires_in_seconds: number;
  refresh_expires_in_seconds: number;
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem("access_token");
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "REQUEST_FAILED", body?.error?.message ?? "Request failed.");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function login(identifier: string, password: string): Promise<void> {
  const tokens = await jsonRequest<TokenResponse>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier, password }),
  });
  sessionStorage.setItem("access_token", tokens.access_token);
  sessionStorage.setItem("refresh_token", tokens.refresh_token);
}

export async function logout(): Promise<void> {
  const refreshToken = sessionStorage.getItem("refresh_token");
  try {
    if (refreshToken) {
      await jsonRequest<void>("/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    }
  } finally {
    sessionStorage.removeItem("access_token");
    sessionStorage.removeItem("refresh_token");
  }
}

export function loadProjects(): Promise<{ items: ProjectSummary[] }> {
  return jsonRequest<{ items: ProjectSummary[] }>("/projects?state=ACTIVE&limit=100");
}

export function hasSession(): boolean {
  return Boolean(sessionStorage.getItem("access_token"));
}

export type { CurrentUser };
