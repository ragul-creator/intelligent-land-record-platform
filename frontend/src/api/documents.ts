import { sessionFetch } from "./session";
import { ApiError } from "./gis";

export interface DocumentSummary {
  id: string;
  project_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: string;
  latest_processing_job_id: string | null;
  uploaded_at: string;
  updated_at: string;
}

export interface OcrRegion {
  text: string;
  confidence: number | null;
  bounding_box: { left: number; top: number; width: number; height: number } | null;
  kind: string;
}

export interface OcrPage {
  page_number: number;
  text: string;
  confidence: number | null;
  width: number;
  height: number;
  requested_languages: string[];
  project_tested_languages: string[];
  preprocessing: { operations: string[]; deskew_angle_degrees: number | null };
  regions: OcrRegion[];
  engine: string;
  engine_version: string | null;
  model_version: string | null;
  processed_at: string;
}

export interface DocumentField {
  id: string;
  field_name: string;
  original_value: string;
  normalized_value: unknown;
  confidence: number | null;
  page_number: number;
  bounding_box: { left: number; top: number; width: number; height: number } | null;
  source_id: string;
  model_version: string | null;
  extractor_version: string;
  processed_at: string;
  corrections: Array<{ id: string; corrected_value: string; reason: string; created_at: string }>;
}

export interface DocumentDetail extends DocumentSummary {
  latest_ocr: {
    id: string;
    version: number;
    confidence: number | null;
    requested_languages: string[];
    project_tested_languages: string[];
    engine: string;
    engine_version: string | null;
    model_version: string | null;
    page_count: number;
    processed_at: string;
    payload: { pages?: OcrPage[] };
  } | null;
  latest_validation: {
    id: string;
    version: number;
    status: string;
    report: { issues?: Array<{ code: string; message: string; severity: string }> };
    confidence_summary: Record<string, unknown>;
    review_task_id: string | null;
  } | null;
}

export interface DocumentSourceUrl {
  document_id: string;
  source_url: string;
  expires_in_seconds: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await sessionFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(
      response.status,
      body?.error?.code ?? "REQUEST_FAILED",
      body?.error?.message ?? "Unable to process document data.",
    );
  }
  return response.json() as Promise<T>;
}

export const loadDocuments = (projectId: string) =>
  request<{ items: DocumentSummary[] }>(`/projects/${projectId}/documents`);

export const loadDocument = (projectId: string, documentId: string) =>
  request<DocumentDetail>(`/projects/${projectId}/documents/${documentId}`);

export const loadDocumentSourceUrl = (projectId: string, documentId: string) =>
  request<DocumentSourceUrl>(`/projects/${projectId}/documents/${documentId}/source-url`);

export const loadFields = (projectId: string, documentId: string) =>
  request<{ fields: DocumentField[] }>(`/projects/${projectId}/documents/${documentId}/fields`);

export const processDocument = (projectId: string, documentId: string, reprocess = false) =>
  request(`/projects/${projectId}/documents/${documentId}/${reprocess ? "reprocess" : "process"}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ languages: "tam+eng" }),
  });

export const correctDocumentField = (
  projectId: string,
  documentId: string,
  fieldId: string,
  correctedValue: string,
  reason: string,
) =>
  request(`/projects/${projectId}/documents/${documentId}/fields/${fieldId}/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ corrected_value: correctedValue, reason }),
  });

export async function uploadDocument(projectId: string, file: File): Promise<DocumentSummary> {
  const body = new FormData();
  body.append("upload", file);
  return request(`/projects/${projectId}/documents`, { method: "POST", body });
}
