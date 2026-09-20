import { ApiError } from "./gis";
import { sessionFetch } from "./session";

export interface SearchItem {
  kind: "DOCUMENT" | "PARCEL" | "FIELD";
  id: string;
  evidence_id: string | null;
  title: string;
  subtitle: string | null;
  status: string | null;
  matched_value: string;
  preliminary: boolean;
}

export interface ExportDescriptor {
  code: "RECORDS_CSV" | "PARCELS_GEOJSON";
  label: string;
  path: string;
  media_type: string;
  description: string;
}

export interface ExportManifest {
  project_id: string;
  disclaimer: string;
  items: ExportDescriptor[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await sessionFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "PLATFORM_REQUEST_FAILED", body?.error?.message ?? "Unable to load project platform data.");
  }
  return response.json() as Promise<T>;
}

export function searchProject(projectId: string, query: string): Promise<{ query: string; items: SearchItem[]; total: number }> {
  const params = new URLSearchParams({ q: query, limit: "50" });
  return request(`/projects/${projectId}/search?${params.toString()}`);
}

export function loadExportManifest(projectId: string): Promise<ExportManifest> {
  return request(`/projects/${projectId}/exports`);
}

export async function downloadProjectExport(descriptor: ExportDescriptor): Promise<void> {
  const response = await sessionFetch(descriptor.path.replace(/^\/api\/v1/, ""));
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "EXPORT_FAILED", body?.error?.message ?? "Unable to generate export.");
  }
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const contentDisposition = response.headers.get("Content-Disposition");
  const filename = contentDisposition?.match(/filename="([^"]+)"/)?.[1] ?? (descriptor.code === "RECORDS_CSV" ? "records.csv" : "parcels.geojson");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
}
