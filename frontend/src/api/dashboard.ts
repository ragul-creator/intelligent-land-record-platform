import { ApiError } from "./gis";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface StatusCount { status: string; count: number; }
export interface ProjectDashboard {
  project: { id: string; name: string; description: string | null; state: "ACTIVE" | "ARCHIVED"; owner_id: string; created_at: string; updated_at: string };
  project_role: "ADMIN" | "OFFICER" | "REVIEWER" | "SURVEYOR" | "VIEWER";
  documents: { total: number; by_status: StatusCount[]; validated_records: number; unlinked_validated_records: number };
  reviews: { open: number; document: number; gis: number; record_parcel_link: number; high: number; medium: number; assigned_to_me: number };
  geo: { imagery_assets: number; geoai_jobs: number; parcels: number; buildings: number; roads: number; land_use_features: number; parcel_statuses: StatusCount[] };
  record_parcel_links: { total: number; by_status: StatusCount[]; confirmed_records: number };
  jobs: { total: number; by_status: StatusCount[] };
  attention: { failed_jobs: number; review_required_documents: number; high_open_reviews: number; ambiguous_record_parcel_links: number; parcels_needing_review: number };
}

export async function loadProjectDashboard(projectId: string): Promise<ProjectDashboard> {
  const token = sessionStorage.getItem("access_token");
  const response = await fetch(`${baseUrl}/api/v1/projects/${projectId}/dashboard`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "DASHBOARD_REQUEST_FAILED", body?.error?.message ?? "Unable to load the project dashboard.");
  }
  return response.json() as Promise<ProjectDashboard>;
}
