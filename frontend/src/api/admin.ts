import { ApiError } from "./gis";
import { sessionFetch } from "./session";

export type ApplicationRole = "ADMIN" | "OFFICER" | "REVIEWER" | "SURVEYOR" | "VIEWER";

export interface ProjectAdminRecord {
  id: string;
  name: string;
  description: string | null;
  state: "ACTIVE" | "ARCHIVED";
  owner_id: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectMember {
  user_id: string;
  login_id: string;
  full_name: string;
  is_active: boolean;
  role: ApplicationRole;
  created_at: string;
}

export interface UserDirectoryItem {
  id: string;
  login_id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  roles: string[];
}

interface Page<T> { items: T[]; page: { limit: number; offset: number; total: number }; }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await sessionFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "ADMIN_REQUEST_FAILED", body?.error?.message ?? "Unable to complete project administration request.");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function createProject(payload: { name: string; description?: string | null }): Promise<ProjectAdminRecord> {
  return request("/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

export function loadProject(projectId: string): Promise<ProjectAdminRecord> {
  return request(`/projects/${projectId}`);
}

export function updateProject(projectId: string, payload: Partial<Pick<ProjectAdminRecord, "name" | "description" | "state">>): Promise<ProjectAdminRecord> {
  return request(`/projects/${projectId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

export async function loadProjectMembers(projectId: string): Promise<Page<ProjectMember>> {
  return request<Page<ProjectMember>>(`/projects/${projectId}/members?limit=100`);
}

export function addProjectMember(projectId: string, userId: string, role: ApplicationRole): Promise<ProjectMember> {
  return request(`/projects/${projectId}/members`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, role }) });
}

export function updateProjectMember(projectId: string, userId: string, role: ApplicationRole): Promise<ProjectMember> {
  return request(`/projects/${projectId}/members/${userId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role }) });
}

export function removeProjectMember(projectId: string, userId: string): Promise<void> {
  return request<void>(`/projects/${projectId}/members/${userId}`, { method: "DELETE" });
}

export function searchUsers(query: string): Promise<Page<UserDirectoryItem>> {
  const params = new URLSearchParams({ q: query, active: "true", limit: "50" });
  return request<Page<UserDirectoryItem>>(`/users?${params.toString()}`);
}
