export type Position = number[];
export interface Geometry { type: string; coordinates: Position[] | Position[][] | Position[][][]; }
export interface ParcelGeometryVersion { id: string; version: number; geometry: Geometry | null; source: string; source_reference: string | null; coordinate_space: string; source_crs: string | null; area_m2: number | null; area_sqft: number | null; change_reason: string | null; validation_status: string | null; created_by_user_id: string | null; created_by_type: "SYSTEM" | "AI" | "HUMAN" | "IMPORT"; processed_at: string | null; created_at: string; }
export interface Parcel { id: string; project_id: string; external_identifier: string | null; source: string; source_reference: string | null; status: string; verification_status: string; current_geometry_version: number; coordinate_space: string; source_crs: string | null; confidence: number | null; model_version: string | null; ai_boundary_status: string | null; requires_survey: boolean; current_version: ParcelGeometryVersion; created_at: string; updated_at: string; }
export interface GeoFeature { id: string; project_id: string; geometry: Geometry; source: string; source_reference: string | null; confidence: number | null; model_version: string | null; status: string; verification_status: string; processed_at: string | null; properties: Record<string, unknown>; }
export type Building = GeoFeature;
export type Road = GeoFeature;
export type LandUseFeature = GeoFeature;
export interface TopologyError { id: string; project_id: string; parcel_id: string | null; related_parcel_id: string | null; code: string; severity: string; area_m2: number | null; message: string; resolved: boolean; created_at: string; }
interface Page<T> { items: T[]; page: { limit: number; offset: number; total: number }; }

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const token = sessionStorage.getItem("access_token");
  const response = await fetch(`${baseUrl}/api/v1${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { message?: string } } | null;
    throw new ApiError(response.status, body?.error?.message ?? "Unable to load GIS data.");
  }
  return response.json() as Promise<T>;
}

export async function loadGisProject(projectId: string) {
  const [parcels, buildings, roads, landUse, topology] = await Promise.all([
    get<Page<Parcel>>(`/projects/${projectId}/parcels?limit=500`), get<Page<Building>>(`/projects/${projectId}/buildings?limit=500`), get<Page<Road>>(`/projects/${projectId}/roads?limit=500`), get<Page<LandUseFeature>>(`/projects/${projectId}/land-use?limit=500`), get<Page<TopologyError>>(`/projects/${projectId}/topology-errors?limit=500`),
  ]);
  return { parcels: parcels.items, buildings: buildings.items, roads: roads.items, landUse: landUse.items, topology: topology.items };
}

export async function loadParcelVersions(projectId: string, parcelId: string): Promise<ParcelGeometryVersion[]> {
  return (await get<Page<ParcelGeometryVersion>>(`/projects/${projectId}/parcels/${parcelId}/versions?limit=100`)).items;
}
