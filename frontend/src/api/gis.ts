export type Position = number[];
export interface Geometry { type: string; coordinates: Position | Position[] | Position[][] | Position[][][]; }
export interface ParcelGeometryVersion { id: string; version: number; geometry: Geometry | null; source: string; source_reference: string | null; coordinate_space: string; source_crs: string | null; area_m2: number | null; area_sqft: number | null; change_reason: string | null; validation_status: string | null; created_by_user_id: string | null; created_by_type: "SYSTEM" | "AI" | "HUMAN" | "IMPORT"; processed_at: string | null; created_at: string; }
export interface Parcel { id: string; project_id: string; external_identifier: string | null; source: string; source_reference: string | null; status: string; verification_status: string; current_geometry_version: number; coordinate_space: string; source_crs: string | null; confidence: number | null; model_version: string | null; ai_boundary_status: string | null; requires_survey: boolean; current_version: ParcelGeometryVersion; created_at: string; updated_at: string; }
export interface GeoFeature { id: string; project_id: string; geometry: Geometry; source: string; source_reference: string | null; confidence: number | null; model_version: string | null; status: string; verification_status: string; processed_at: string | null; properties: Record<string, unknown>; }
export type Building = GeoFeature;
export type Road = GeoFeature;
export type LandUseFeature = GeoFeature;
export interface TopologyError { id: string; project_id: string; parcel_id: string | null; related_parcel_id: string | null; code: string; severity: string; area_m2: number | null; message: string; resolved: boolean; created_at: string; }
export interface CurrentUser { id: string; login_id: string; email: string; full_name: string; roles: string[]; permissions: string[]; project_memberships: Array<{ project_id: string; role: string }>; }
export interface ParcelVersionSaveResult { version: ParcelGeometryVersion; status: "VALID" | "REVIEW_REQUIRED"; area_before_m2: number | null; area_after_m2: number | null; issues: Array<{ code: string; severity: string; message: string; area_m2: number | null }>; }
interface Page<T> { items: T[]; page: { limit: number; offset: number; total: number }; }

export class ApiError extends Error { constructor(public status: number, public code: string, message: string) { super(message); } }
const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem("access_token");
  const response = await fetch(`${baseUrl}/api/v1${path}`, { ...init, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? "REQUEST_FAILED", body?.error?.message ?? "Unable to load GIS data.");
  }
  return response.json() as Promise<T>;
}

const get = <T>(path: string) => request<T>(path);

export async function loadGisProject(projectId: string) {
  const [parcels, buildings, roads, landUse, topology] = await Promise.all([
    get<Page<Parcel>>(`/projects/${projectId}/parcels?limit=500`), get<Page<Building>>(`/projects/${projectId}/buildings?limit=500`), get<Page<Road>>(`/projects/${projectId}/roads?limit=500`), get<Page<LandUseFeature>>(`/projects/${projectId}/land-use?limit=500`), get<Page<TopologyError>>(`/projects/${projectId}/topology-errors?limit=500`),
  ]);
  return { parcels: parcels.items, buildings: buildings.items, roads: roads.items, landUse: landUse.items, topology: topology.items };
}

export async function loadParcelVersions(projectId: string, parcelId: string): Promise<ParcelGeometryVersion[]> {
  return (await get<Page<ParcelGeometryVersion>>(`/projects/${projectId}/parcels/${parcelId}/versions?limit=100`)).items;
}

export function loadCurrentUser(): Promise<CurrentUser> { return get<CurrentUser>("/users/me"); }

export function createParcelVersion(projectId: string, parcelId: string, payload: { geometry: Geometry; expected_current_version: number; change_reason: string }): Promise<ParcelVersionSaveResult> {
  return request<ParcelVersionSaveResult>(`/projects/${projectId}/parcels/${parcelId}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, source_crs: "EPSG:4326", coordinate_space: "WORLD" }) });
}
