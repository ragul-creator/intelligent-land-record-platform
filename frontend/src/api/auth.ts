import { ApiError, type CurrentUser } from "./gis";
import {
  apiBaseUrl,
  clearSessionTokens,
  hasStoredSession,
  sessionFetch,
  storeSessionTokens,
  type SessionTokens,
} from "./session";

export interface ProjectSummary {
  id: string;
  name: string;
  description: string | null;
  state: "ACTIVE" | "ARCHIVED";
  owner_id: string;
  created_at: string;
  updated_at: string;
}

async function parseError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
  return new ApiError(
    response.status,
    body?.error?.code ?? "REQUEST_FAILED",
    body?.error?.message ?? "Request failed.",
  );
}

export async function login(identifier: string, password: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier, password }),
  });
  if (!response.ok) throw await parseError(response);
  storeSessionTokens(await response.json() as SessionTokens);
}

export async function logout(): Promise<void> {
  const refreshToken = sessionStorage.getItem("refresh_token");
  try {
    if (refreshToken) {
      await fetch(`${apiBaseUrl()}/api/v1/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    }
  } finally {
    clearSessionTokens();
  }
}

export async function loadProjects(): Promise<{ items: ProjectSummary[] }> {
  const response = await sessionFetch("/projects?state=ACTIVE&limit=100");
  if (!response.ok) throw await parseError(response);
  return response.json() as Promise<{ items: ProjectSummary[] }>;
}

export function hasSession(): boolean {
  return hasStoredSession();
}

export type { CurrentUser };
