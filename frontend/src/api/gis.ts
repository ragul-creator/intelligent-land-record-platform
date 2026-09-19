export type Position = number[];
export interface Geometry { type: string; coordinates: Position | Position[] | Position[][] | Position[][][]; }
export interface ParcelGeometryVersion { id: string; version: number; geometry: Geometry | null; source: string; source_reference: string | null; coordinate_space: string; source_crs: string | null; area_m2: number | null; area_sqft: number | null; change_reason: string | null; validation_status: string | null; created_by_user_id: string | null; created_by_type: "SYSTEM" | "AI" | "HUMAN" | "IMPORT"; processed_at: string | null; created_at: string; }
export interface Parcel { id: string; project_id: string; external_identifier: string | null; source: string; source_reference: string | null; status: string; verification_status: string; current_geometry_version: number; coordinate_space: string; source_crs: string | null; confidence: number | null; model_version: string | null; ai_boundary_status: string | null; requires_survey: boolean; current_version: ParcelGeometryVersion; created_at: string; updated_at: string; }
export interface GeoFeature { id: string; project_id: string; geometry: Geometry; source: string; source_reference: string | null; confidence: number | null; model_version: string | null; status: string; verification_status: string; processed_at: string | null; properties: Record<string, unknown>; }
export type Building = GeoFeature;
export type Road = GeoFeature;
export type LandUseFeature = GeoFeature;
export interface TopologyError { id: string; project_id: string; parcel_id: string | null; related_parcel_id: string | null; code: string; severity: string; area_m2: number | null; message: string; resolved: boolean; created_at: string; }
export interface ImageryAsset { id: string; project_id: string; file_id: string | null; filename: string | null; source_reference: string | null; source_crs: string | null; coordinate_space: string; metadata: Record<string, unknown>; registration_job_id: string | null; created_at: string; updated_at: string; }
export interface ImageryPreview { imagery_asset_id: string; preview_url: string; corners_wgs84: Array<[number, number]>; expires_in_seconds: number; }
export interface GeoAIJob { id: string; project_id: string; job_type: string; status: "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED" | "CANCELLED"; progress: number; has_error: boolean; output_references: Record<string, unknown>; created_at: string; updated_at: string; }
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
  const [parcels, buildings, roads, landUse, topology, imagery] = await Promise.all([
    get<Page<Parcel>>(`/projects/${projectId}/parcels?limit=100`), get<Page<Building>>(`/projects/${projectId}/buildings?limit=500`), get<Page<Road>>(`/projects/${projectId}/roads?limit=500`), get<Page<LandUseFeature>>(`/projects/${projectId}/land-use?limit=500`), get<Page<TopologyError>>(`/projects/${projectId}/topology-errors?limit=500`),
    get<Page<ImageryAsset>>(`/projects/${projectId}/imagery?limit=50`),
  ]);
  return { parcels: parcels.items, buildings: buildings.items, roads: roads.items, landUse: landUse.items, topology: topology.items, imagery: imagery.items };
}

export async function loadParcelVersions(projectId: string, parcelId: string): Promise<ParcelGeometryVersion[]> {
  return (await get<Page<ParcelGeometryVersion>>(`/projects/${projectId}/parcels/${parcelId}/versions?limit=100`)).items;
}

export function loadCurrentUser(): Promise<CurrentUser> { return get<CurrentUser>("/users/me"); }

export function createParcelVersion(projectId: string, parcelId: string, payload: { geometry: Geometry; expected_current_version: number; change_reason: string }): Promise<ParcelVersionSaveResult> {
  return request<ParcelVersionSaveResult>(`/projects/${projectId}/parcels/${parcelId}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, source_crs: "EPSG:4326", coordinate_space: "WORLD" }) });
}

export function loadImageryPreview(projectId: string, assetId: string): Promise<ImageryPreview> { return get<ImageryPreview>(`/projects/${projectId}/imagery/${assetId}/preview-url`); }

export function createGeoAIJob(projectId: string, payload: { job_type: "BUILDING_VECTORIZE" | "PARCEL_IMPORT"; source_type: string; source_payload: Record<string, unknown>; source_crs?: string; source_reference?: string | null; imagery_asset_id?: string; idempotency_key?: string }): Promise<GeoAIJob> {
  return request<GeoAIJob>(`/projects/${projectId}/geoai/jobs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

export function loadGeoAIJob(projectId: string, jobId: string): Promise<GeoAIJob> { return get<GeoAIJob>(`/projects/${projectId}/geoai/jobs/${jobId}`); }

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, "0")).join("");
}

export async function uploadAndRegisterImagery(projectId: string, file: File, sourceReference?: string): Promise<ImageryAsset> {
  const allowed = ["image/tiff", "application/geotiff"];
  if (!allowed.includes(file.type) || !/\.tiff?$/i.test(file.name)) throw new ApiError(422, "IMAGERY_SOURCE_INVALID", "Select a GeoTIFF (.tif or .tiff) file.");
  const registration = await request<{ file_id: string; upload_url: string; required_headers: Record<string, string> }>("/files/presign", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: projectId, filename: file.name, category: "IMAGERY", content_type: file.type, size_bytes: file.size, sha256: await sha256(file) }) });
  let upload: Response;
  try {
    upload = await fetch(registration.upload_url, { method: "PUT", headers: registration.required_headers, body: file });
  } catch {
    throw new ApiError(0, "IMAGERY_UPLOAD_FAILED", "The browser could not reach private object storage. Check the MinIO upload endpoint and CORS configuration.");
  }
  if (!upload.ok) throw new ApiError(upload.status, "IMAGERY_UPLOAD_FAILED", "The imagery upload could not be completed.");
  await request("/files/complete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file_id: registration.file_id }) });
  return request<ImageryAsset>(`/projects/${projectId}/imagery`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file_id: registration.file_id, source_reference: sourceReference || null }) });
}
