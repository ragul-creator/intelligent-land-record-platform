import { sessionFetch } from "./session";
import { ApiError } from "./gis";

export interface StatusCount { status: string; count: number; }
export interface ProjectDashboard {
  project: { id: string; name: string; description: string | null; state: "ACTIVE" | "ARCHIVED"; owner_id: string; created_at: string; updated_at: string };
  project_role: "ADMIN" | "OFFICER" | "REVIEWER" | "SURVEYOR" | "VIEWER";
  documents: { total: number; by_status: StatusCount[]; validated_records: number; unlinked_validated_records: number };
  reviews: { open: number; document: number; gis: number; record_parcel_link: number; high: number; medium: number; assigned_to_me: number; validation_issues: number };
  geo: { imagery_assets: number; geoai_jobs: number; parcels: number; buildings: number; roads: number; land_use_features: number; parcel_statuses: StatusCount[] };
  record_parcel_links: { total: number; by_status: StatusCount[]; confirmed_records: number };
  jobs: { total: number; by_status: StatusCount[]; active: number; failed: number; retryable_failed: number };
  attention: { failed_jobs: number; review_required_documents: number; high_open_reviews: number; ambiguous_record_parcel_links: number; parcels_needing_review: number; open_validation_issues: number };
  visibility: { project_role: "ADMIN" | "OFFICER" | "REVIEWER" | "SURVEYOR" | "VIEWER"; draft_data_visible: boolean; viewer_read_only: boolean; notice: string };
}

export async function loadProjectDashboard(projectId: string): Promise<ProjectDashboard> {
  const response = await sessionFetch(`/projects/${projectId}/dashboard`);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "DASHBOARD_REQUEST_FAILED", body?.error?.message ?? "Unable to load the project dashboard.");
  }
  return response.json() as Promise<ProjectDashboard>;
}
