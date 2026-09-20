import { sessionFetch } from "./session";
import { ApiError } from "./gis";

export type RecordParcelLinkStatus = "SUGGESTED" | "REVIEW_REQUIRED" | "CONFIRMED" | "REJECTED";
export type RecordParcelLinkMethod = "EXACT_SURVEY_IDENTIFIER" | "ATTRIBUTE_MATCH" | "SPATIAL_CONTEXT" | "MANUAL";

export interface RecordParcelLink {
  id: string;
  project_id: string;
  document_id: string;
  document_validation_result_id: string;
  parcel_id: string;
  parcel_display_identifier: string | null;
  link_status: RecordParcelLinkStatus;
  link_method: RecordParcelLinkMethod;
  confidence: number | null;
  rationale: Record<string, unknown>;
  provenance: Record<string, unknown>;
  review_required: boolean;
  review_task_id: string | null;
  review_reason: string | null;
  created_at: string;
  updated_at: string;
}

interface Page<T> { items: T[]; page: { limit: number; offset: number; total: number }; }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem("access_token");
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "RECORD_LINK_REQUEST_FAILED", body?.error?.message ?? "Unable to load record-to-parcel links.");
  }
  return response.json() as Promise<T>;
}

export function loadDocumentRecordLinks(projectId: string, documentId: string): Promise<Page<RecordParcelLink>> {
  return request<Page<RecordParcelLink>>(`/projects/${projectId}/documents/${documentId}/record-parcel-links?limit=100`);
}

export function suggestDocumentRecordLinks(projectId: string, documentId: string, validationResultId: string): Promise<{ items: RecordParcelLink[] }> {
  return request(`/projects/${projectId}/documents/${documentId}/record-parcel-links/suggestions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_validation_result_id: validationResultId }),
  });
}

export function resolveRecordParcelLink(projectId: string, linkId: string, action: "confirm" | "reject", reason?: string): Promise<RecordParcelLink> {
  return request(`/projects/${projectId}/record-parcel-links/${linkId}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason?.trim() || null }),
  });
}

export function loadParcelRecordLinks(projectId: string, parcelId: string): Promise<Page<RecordParcelLink>> {
  return request<Page<RecordParcelLink>>(`/projects/${projectId}/parcels/${parcelId}/record-parcel-links?limit=100`);
}
