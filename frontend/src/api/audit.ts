import { ApiError } from "./gis";
import { sessionFetch } from "./session";

export interface AuditEvent {
  id: string;
  actor_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

interface Page<T> { items: T[]; page: { limit: number; offset: number; total: number }; }

async function request<T>(path: string): Promise<T> {
  const response = await sessionFetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "AUDIT_REQUEST_FAILED", body?.error?.message ?? "Unable to load audit events.");
  }
  return response.json() as Promise<T>;
}

export function loadAudit(projectId: string, filters?: { action?: string; targetType?: string; actorId?: string }): Promise<Page<AuditEvent>> {
  const params = new URLSearchParams({ limit: "100", offset: "0" });
  if (filters?.action) params.set("action", filters.action);
  if (filters?.targetType) params.set("target_type", filters.targetType);
  if (filters?.actorId) params.set("actor_id", filters.actorId);
  return request<Page<AuditEvent>>(`/projects/${projectId}/audit?${params.toString()}`);
}
