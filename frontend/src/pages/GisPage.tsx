import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { loadCurrentUser, loadGisProject, loadParcelVersions, type GeoFeature, type ImageryPreview, type Parcel, type ParcelVersionSaveResult, type TopologyError } from "../api/gis";
import { GisMap, type BasemapStyle, type LayerVisibility, type MapFeatureKind } from "../components/GisMap";
import { ParcelEditor } from "../components/ParcelEditor";
import { ParcelDrawPanel } from "../components/ParcelDrawPanel";
import { ImageryGeoAiPanel } from "../components/ImageryGeoAiPanel";
import { SavedWebGisDemoPanel, type SavedWebGisDemoCardId } from "../components/SavedWebGisDemoPanel";
import { useParcelEditor } from "../features/gis/useParcelEditor";
import { useParcelDraw } from "../features/gis/useParcelDraw";
import { loadParcelRecordLinks } from "../api/recordLinks";

const initialLayers: LayerVisibility = { basemap: true, parcels: true, buildings: true, roads: true, landUse: true, topology: true };
const measure = (value: number | null) => value === null ? "Not available" : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
const layerStorageKey = (projectId: string) => `gis-layer-visibility:${projectId}`;

type SavedWebGisDemo = {
  id: SavedWebGisDemoCardId;
  title: string;
  filename: string;
  preview_url: string;
  source_url: string | null;
  imagery_asset_id: string;
  corners_wgs84: Array<[number, number]>;
  metadata: Record<string, unknown>;
  summary: Record<string, unknown>;
  jobs: Array<{ job_type: string; metrics: Record<string, unknown>; feature_count: number | null; model_version: string | null; created_at: string }>;
  buildings: GeoFeature[];
  roads: GeoFeature[];
  parcels: Parcel[];
  landUse: GeoFeature[];
  topology: TopologyError[];
};

type SavedWebGisDemoSet = {
  project_name: string;
  captured_at: string;
  display_note: string;
  demos: SavedWebGisDemo[];
};

async function loadSavedWebGisDemoSet(): Promise<SavedWebGisDemoSet> {
  const response = await fetch("/demo/webgis-demo-result.json");
  if (!response.ok) throw new Error("Saved WebGIS demonstration is unavailable.");
  return response.json() as Promise<SavedWebGisDemoSet>;
}

function featuresForActiveImagery(features: GeoFeature[], imageryAssetId: string | null | undefined): GeoFeature[] {
  if (!imageryAssetId) return features;
  const activeReference = `imagery:${imageryAssetId}`;
  return features.filter((item) => {
    const isUnverifiedImageryCandidate = item.source === "AI_CANDIDATE"
      && item.status === "AI_PRELIMINARY"
      && item.verification_status === "UNVERIFIED"
      && item.source_reference?.startsWith("imagery:");
    return !isUnverifiedImageryCandidate || item.source_reference === activeReference;
  });
}

export function buildingsForActiveImagery(buildings: GeoFeature[], imageryAssetId: string | null | undefined): GeoFeature[] {
  return featuresForActiveImagery(buildings, imageryAssetId);
}

export function roadsForActiveImagery(roads: GeoFeature[], imageryAssetId: string | null | undefined): GeoFeature[] {
  return featuresForActiveImagery(roads, imageryAssetId);
}

export function landUseForActiveImagery(landUse: GeoFeature[], imageryAssetId: string | null | undefined): GeoFeature[] {
  return featuresForActiveImagery(landUse, imageryAssetId);
}

export function parcelsForActiveImagery(parcels: Parcel[], imageryAssetId: string | null | undefined): Parcel[] {
  if (!imageryAssetId) return parcels;
  const activeReference = `imagery:${imageryAssetId}:parcel-candidate`;
  return parcels.filter((parcel) => {
    const isUnverifiedAiCandidate = parcel.source === "AI_VISIBLE_BOUNDARY"
      && parcel.verification_status === "UNVERIFIED"
      && parcel.source_reference?.startsWith("imagery:");
    return !isUnverifiedAiCandidate || parcel.source_reference === activeReference;
  });
}

function readLayerVisibility(projectId: string): LayerVisibility {
  try {
    const stored = localStorage.getItem(layerStorageKey(projectId));
    if (!stored) return initialLayers;
    return { ...initialLayers, ...(JSON.parse(stored) as Partial<LayerVisibility>) };
  } catch {
    return initialLayers;
  }
}

function ParcelPanel({ projectId, parcel, issues, editor, canEdit, onSaved, demoMode = false }: { projectId: string; parcel: Parcel | null; issues: TopologyError[]; editor: ReturnType<typeof useParcelEditor>; canEdit: boolean; onSaved: (result: ParcelVersionSaveResult) => void; demoMode?: boolean }) {
  const versions = useQuery({ queryKey: ["parcel-versions", projectId, parcel?.id], queryFn: () => loadParcelVersions(projectId, parcel!.id), enabled: Boolean(parcel && !demoMode) });
  const recordLinks = useQuery({ queryKey: ["parcel-record-links", projectId, parcel?.id], queryFn: () => loadParcelRecordLinks(projectId, parcel!.id), enabled: Boolean(parcel && !demoMode), retry: false });
  if (!parcel) return <aside className="detail-panel empty-panel"><h2>Feature details</h2><p>Select a parcel boundary to inspect its draft metadata. Building footprints remain separate from parcel boundaries.</p></aside>;
  const version = parcel.current_version;
  const candidateProperties = version.properties ?? {};
  const buildingCount = typeof candidateProperties.building_count === "number" ? candidateProperties.building_count : null;
  const roadFrontage = typeof candidateProperties.road_frontage_m === "number" ? candidateProperties.road_frontage_m : null;
  const candidateMethod = typeof candidateProperties.method === "string" ? candidateProperties.method : null;
  const candidateKind = typeof candidateProperties.candidate_kind === "string" ? candidateProperties.candidate_kind : null;
  const confidenceTier = typeof candidateProperties.confidence_tier === "string" ? candidateProperties.confidence_tier : null;
  const frontageSupported = typeof candidateProperties.frontage_supported === "boolean" ? candidateProperties.frontage_supported : null;
  const boundarySummaryValue = candidateProperties.boundary_evidence_summary;
  const boundarySummary = boundarySummaryValue && typeof boundarySummaryValue === "object" && !Array.isArray(boundarySummaryValue) ? boundarySummaryValue as Record<string, unknown> : null;
  const boundaryPrimaryType = typeof boundarySummary?.primary_type === "string" ? boundarySummary.primary_type : null;
  const boundarySupportedFraction = typeof boundarySummary?.supported_fraction === "number" ? boundarySummary.supported_fraction : null;
  const boundaryEvidenceModel = typeof candidateProperties.boundary_evidence_model_version === "string" ? candidateProperties.boundary_evidence_model_version : null;
  const candidateWarnings = Array.isArray(candidateProperties.warnings) ? candidateProperties.warnings.filter((item): item is string => typeof item === "string") : [];
  return <aside className="detail-panel" aria-label="Parcel details"><p className="eyebrow">Draft parcel</p><h2>{parcel.external_identifier ?? "Preliminary plot candidate"}</h2><div className="status-row"><span>{parcel.status}</span><span>{parcel.verification_status}</span>{parcel.ai_boundary_status && <span>{parcel.ai_boundary_status}</span>}</div><dl><dt>Parcel ID</dt><dd>{parcel.id}</dd><dt>Source</dt><dd>{parcel.source}</dd><dt>Source reference</dt><dd>{parcel.source_reference ?? "Not supplied"}</dd><dt>Current version</dt><dd>{parcel.current_geometry_version}</dd><dt>Source CRS</dt><dd>{parcel.source_crs ?? "No declared CRS"}</dd><dt>Processed at</dt><dd>{version.processed_at ? new Date(version.processed_at).toLocaleString() : "Not supplied"}</dd><dt>Confidence</dt><dd>{parcel.confidence === null ? "Not available" : `${Math.round(parcel.confidence * 100)}%`}</dd><dt>Model version</dt><dd>{parcel.model_version ?? "Not applicable"}</dd><dt>Area</dt><dd>{measure(version.area_m2)} m²</dd><dt>Area</dt><dd>{measure(version.area_sqft)} sq ft</dd><dt>Survey required</dt><dd>{parcel.requires_survey ? "Yes" : "No"}</dd><dt>Topology issues</dt><dd><strong className={issues.length ? "issue-count" : ""}>{issues.length}</strong></dd></dl>{parcel.source === "AI_VISIBLE_BOUNDARY" && <section className="parcel-candidate-summary" aria-label="AI plot candidate summary"><h3>AI plot candidate</h3><dl><dt>Plot type</dt><dd>{candidateKind === "VACANT_OPEN_REVIEW" ? "Vacant / open land — review required" : candidateKind === "BUILDING_ASSOCIATED" ? "Building-associated" : "Preliminary"}</dd><dt>Method</dt><dd>{candidateMethod?.replaceAll("_", " ") ?? "Road + building subdivision"}</dd><dt>Evidence quality</dt><dd>{confidenceTier ?? "Not rated"}</dd><dt>Detected buildings</dt><dd>{buildingCount ?? "Not available"}</dd><dt>Estimated road frontage</dt><dd>{roadFrontage === null ? "Not available" : `${measure(roadFrontage)} m`}</dd><dt>Frontage support</dt><dd>{frontageSupported === null ? "Not determined" : frontageSupported ? "Detected" : "Weak / not detected"}</dd><dt>Primary boundary evidence</dt><dd>{boundaryPrimaryType?.replaceAll("_", " ") ?? "Not analysed"}</dd><dt>Visible boundary support</dt><dd>{boundarySupportedFraction === null ? "Not analysed" : `${Math.round(boundarySupportedFraction * 100)}%`}</dd><dt>Boundary evidence model</dt><dd>{boundaryEvidenceModel ?? "Not analysed"}</dd><dt>Boundary status</dt><dd>Preliminary — survey/FMB verification required</dd></dl>{candidateWarnings.length > 0 && <ul>{candidateWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}</section>}{issues.length > 0 && <section className="topology-list" aria-label="Unresolved topology issues"><h3>Unresolved topology findings</h3>{issues.map((issue) => <article key={issue.id}><strong>{issue.severity} · {issue.code}</strong><span>{issue.message}</span>{issue.area_m2 !== null && <span>{measure(issue.area_m2)} m² affected</span>}</article>)}</section>}<section className="parcel-record-links"><h3>Linked land records</h3>{recordLinks.isLoading && <p>Loading record associations…</p>}{recordLinks.isError && <p className="error-copy">Record associations are unavailable for this parcel.</p>}{recordLinks.data?.items.length === 0 && <p>No validated land record is associated with this parcel yet.</p>}{recordLinks.data?.items.map((link) => <article key={link.id}><strong>{link.link_status.replaceAll("_", " ")}</strong><span>{link.link_method.replaceAll("_", " ")} · confidence {link.confidence === null ? "not available" : `${Math.round(link.confidence * 100)}%`}</span><Link to={`/projects/${projectId}/documents?documentId=${link.document_id}`}>Open source record</Link></article>)}</section><ParcelEditor projectId={projectId} parcel={parcel} editor={editor} canEdit={canEdit} onSaved={onSaved} /><h3>Version history</h3>{versions.isLoading && <p>Loading versions…</p>}{versions.isError && <p className="error-copy">Version history is unavailable. Refresh the GIS data and try again.</p>}{versions.data && <ol className="version-list">{versions.data.map((item) => <li key={item.id}><strong>v{item.version}</strong><span>{item.created_by_type} · {item.created_by_user_id ?? "system"} · {new Date(item.created_at).toLocaleDateString()}</span><span>{measure(item.area_m2)} m² · {measure(item.area_sqft)} sq ft</span><span>{item.source} · {item.source_reference ?? "No source reference"}</span>{item.change_reason && <span>{item.change_reason}</span>}</li>)}</ol>}<p className="panel-note">Draft geometry remains provisional and requires authorized verification. Saving appends a new immutable version.</p></aside>;
}

function FeaturePanel({ kind, feature }: { kind: MapFeatureKind; feature: GeoFeature | null }) {
  if (!feature) return <aside className="detail-panel empty-panel"><h2>Feature details</h2><p>The selected GIS feature is no longer available. Refresh the map or select another feature.</p></aside>;
  const title = kind === "ROAD" ? String(feature.properties.road_class ?? "Road or pathway") : kind === "LAND_USE" ? String(feature.properties.land_use_class ?? "Land use") : "Building footprint";
  return <aside className="detail-panel" aria-label={`${kind.toLowerCase()} details`}><p className="eyebrow">{kind.replace("_", " ")}</p><h2>{title}</h2><div className="status-row"><span>{feature.status}</span><span>{feature.verification_status}</span></div><dl><dt>Feature ID</dt><dd>{feature.id}</dd><dt>Source</dt><dd>{feature.source}</dd><dt>Source reference</dt><dd>{feature.source_reference ?? "Not supplied"}</dd><dt>Confidence</dt><dd>{feature.confidence === null ? "Not available" : `${Math.round(feature.confidence * 100)}%`}</dd><dt>Model version</dt><dd>{feature.model_version ?? "Not applicable"}</dd><dt>Processed at</dt><dd>{feature.processed_at ? new Date(feature.processed_at).toLocaleString() : "Not supplied"}</dd>{kind === "ROAD" && <><dt>Length</dt><dd>{measure(feature.properties.length_m as number | null)} m</dd></>}{kind !== "ROAD" && <><dt>Area</dt><dd>{measure(feature.properties.area_m2 as number | null)} m²</dd><dt>Area</dt><dd>{measure(feature.properties.area_sqft as number | null)} sq ft</dd></>}</dl><p className="panel-note">Read-only GIS context. This feature is not a legal parcel determination.</p></aside>;
}

export function GisPage() {
  const { projectId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const editor = useParcelEditor();
  const drawer = useParcelDraw();
  const [layers, setLayers] = useState<LayerVisibility>(() => projectId ? readLayerVisibility(projectId) : initialLayers);
  const [basemapStyle, setBasemapStyle] = useState<BasemapStyle>("STREET");
  const [selected, setSelected] = useState<{ kind: "PARCEL" | MapFeatureKind; id: string } | null>(() => searchParams.get("parcelId") ? { kind: "PARCEL", id: searchParams.get("parcelId")! } : null);
  const [saveResult, setSaveResult] = useState<ParcelVersionSaveResult | null>(null);
  const [imageryPreview, setImageryPreview] = useState<ImageryPreview | null>(null);
  const [imageryZoomRequest, setImageryZoomRequest] = useState(0);
  const mapStageRef = useRef<HTMLDivElement>(null);
  const [mapFullscreen, setMapFullscreen] = useState(false);
  const [savedDemoId, setSavedDemoId] = useState<SavedWebGisDemoCardId | null>(() => {
    const requested = searchParams.get("savedDemo");
    return requested === "vegas-geoai" || requested === "tamilnadu-lulc" ? requested : null;
  });
  const toggleMapFullscreen = useCallback(async () => {
    const stage = mapStageRef.current;
    if (!stage) return;
    if (document.fullscreenElement === stage) {
      await document.exitFullscreen();
    } else {
      await stage.requestFullscreen();
    }
  }, []);
  const requestImageryZoom = useCallback(() => {
    setImageryZoomRequest((value) => value + 1);
  }, []);

  useEffect(() => {
    if (projectId) setLayers(readLayerVisibility(projectId));
  }, [projectId]);
  useEffect(() => {
    const onFullscreenChange = () => setMapFullscreen(document.fullscreenElement === mapStageRef.current);
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);
  useEffect(() => {
    const parcelId = searchParams.get("parcelId");
    if (parcelId) setSelected({ kind: "PARCEL", id: parcelId });
  }, [searchParams]);
  useEffect(() => {
    if (!projectId) return;
    try { localStorage.setItem(layerStorageKey(projectId), JSON.stringify(layers)); } catch { /* Preferences are best effort only. */ }
  }, [layers, projectId]);

  const query = useQuery({
    queryKey: ["gis-project", projectId],
    queryFn: () => loadGisProject(projectId!),
    enabled: Boolean(projectId),
    refetchOnWindowFocus: !editor.session,
    refetchInterval: editor.session ? false : 30_000,
  });
  const currentUser = useQuery({ queryKey: ["current-user"], queryFn: loadCurrentUser, retry: false, enabled: Boolean(projectId) });
  const savedDemoSet = useQuery({
    queryKey: ["saved-webgis-demo"],
    queryFn: loadSavedWebGisDemoSet,
    enabled: Boolean(savedDemoId),
    retry: false,
    staleTime: Infinity,
  });
  const savedDemo = savedDemoId ? savedDemoSet.data?.demos?.find((demo) => demo.id === savedDemoId) ?? null : null;

  useEffect(() => {
    if (query.isError && !query.data && !savedDemoId) {
      setSavedDemoId("vegas-geoai");
      setSearchParams({ savedDemo: "vegas-geoai" }, { replace: true });
    }
  }, [query.isError, query.data, savedDemoId, setSearchParams]);

  if (!projectId) return <main className="gis-state"><h1>Project GIS unavailable</h1></main>;
  if (!savedDemoId && query.isLoading) return <main className="gis-state"><p className="eyebrow">Web-GIS</p><h1>Loading project layers…</h1></main>;

  const liveUnavailable = Boolean(query.isError && !query.data);
  const liveData = query.data ?? {
    parcels: [],
    buildings: [],
    roads: [],
    landUse: [],
    topology: [],
    imagery: [],
  };
  const data = savedDemo ? {
    parcels: savedDemo.parcels,
    buildings: savedDemo.buildings,
    roads: savedDemo.roads,
    landUse: savedDemo.landUse,
    topology: savedDemo.topology,
    imagery: [],
  } : liveData;
  const activeImageryAssetId = savedDemo?.imagery_asset_id ?? imageryPreview?.imagery_asset_id;
  const mapBuildings = buildingsForActiveImagery(data.buildings, activeImageryAssetId);
  const mapRoads = roadsForActiveImagery(data.roads, activeImageryAssetId);
  const mapLandUse = landUseForActiveImagery(data.landUse, activeImageryAssetId);
  const mapParcels = parcelsForActiveImagery(data.parcels, activeImageryAssetId);
  const selectedParcel = selected?.kind === "PARCEL" ? data.parcels.find((parcel) => parcel.id === selected.id) ?? null : null;
  const selectedFeature = selected?.kind === "BUILDING" ? data.buildings.find((item) => item.id === selected.id) ?? null : selected?.kind === "ROAD" ? data.roads.find((item) => item.id === selected.id) ?? null : selected?.kind === "LAND_USE" ? data.landUse.find((item) => item.id === selected.id) ?? null : null;
  const selectedIssues = selectedParcel ? data.topology.filter((issue) => issue.parcel_id === selectedParcel.id || issue.related_parcel_id === selectedParcel.id) : [];
  const empty = !mapParcels.length && !mapBuildings.length && !mapRoads.length && !mapLandUse.length;
  const topologyParcelIds = [...new Set(data.topology.flatMap((issue) => [issue.parcel_id, issue.related_parcel_id]).filter((id): id is string => Boolean(id)))];
  const projectMembership = currentUser.data?.project_memberships?.find((item) => item.project_id === projectId);
  const membership = Boolean(projectMembership);
  const viewerReadOnly = projectMembership?.role === "VIEWER";
  const canEdit = Boolean(!savedDemo && currentUser.data?.permissions?.includes("geo:edit_draft") && membership);
  const canUploadImagery = Boolean(!savedDemo && currentUser.data?.permissions?.includes("imagery:upload") && membership);
  const canProcessGeoAi = Boolean(!savedDemo && currentUser.data?.permissions?.includes("geoai:process") && membership);
  const editOverlay = editor.session ? { original: editor.session.original, working: editor.session.working, showOriginal: editor.session.showOriginal, selectedVertex: editor.session.selectedVertex, onSelectVertex: editor.selectVertex, onMoveVertex: editor.moveVertex, onCommitDrag: editor.commitDraggedVertex, onAddVertex: editor.addVertexAt } : null;
  const lastSyncedAt = query.dataUpdatedAt ? new Date(query.dataUpdatedAt) : null;

  const saved = async (result: ParcelVersionSaveResult) => {
    setSaveResult(result);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["gis-project", projectId] }),
      queryClient.invalidateQueries({ queryKey: ["parcel-versions", projectId, selectedParcel?.id] }),
    ]);
  };
  const refresh = async () => {
    setSaveResult(null);
    if (savedDemoId) {
      await savedDemoSet.refetch();
      return;
    }
    await query.refetch();
    if (selectedParcel) await queryClient.invalidateQueries({ queryKey: ["parcel-versions", projectId, selectedParcel.id] });
  };

  const savedDemoPreview = savedDemo?.corners_wgs84.length === 4
    ? { url: savedDemo.preview_url, corners: savedDemo.corners_wgs84 as [[number, number], [number, number], [number, number], [number, number]] }
    : null;
  const mapPreview = savedDemoPreview ?? (imageryPreview?.corners_wgs84.length === 4 ? { url: imageryPreview.preview_url, corners: imageryPreview.corners_wgs84 as [[number, number], [number, number], [number, number], [number, number]] } : null);
  return <main className="gis-shell"><header className="gis-header"><div><p className="eyebrow">Spatial intelligence · H.2B.1</p><h1>Project cadastral viewer</h1><p>Inspect imagery, preliminary parcel candidates, buildings, roads, land use, and topology evidence in one map workspace.</p><div className="gis-flow-nav"><Link to={`/projects/${projectId}`}>Dashboard</Link><Link to={`/projects/${projectId}/documents`}>Documents</Link><Link to={`/projects/${projectId}/review`}>Review workspace</Link></div><div className="sync-row"><button type="button" onClick={refresh} disabled={(savedDemoId ? savedDemoSet.isFetching : query.isFetching) || Boolean(editor.session) || drawer.points !== null}>{savedDemoId ? (savedDemoSet.isFetching ? "Reloading saved demo…" : "Reload saved demo") : (query.isFetching ? "Refreshing…" : "Refresh GIS data")}</button><span>{editor.session || drawer.points !== null ? "Automatic refresh paused while editing or drawing" : lastSyncedAt ? `Last synced ${lastSyncedAt.toLocaleTimeString()}` : "Waiting for first sync"}</span></div>{query.isError && query.data && !savedDemoId && <p className="sync-warning" role="status">Latest refresh failed. Showing the most recently loaded GIS data.</p>}{liveUnavailable && !savedDemoId && <p className="sync-warning" role="status">Live project layers are unavailable on this deployment. Open one of the preserved GeoAI demonstrations below.</p>}{currentUser.isError && !savedDemoId && <p className="sync-warning" role="status">Editing permissions could not be verified. The map remains read-only until the user session can be checked.</p>}{viewerReadOnly && <p className="viewer-visibility-note" role="status">Viewer policy: draft and unverified GIS evidence is visible for inspection only. Verification labels remain visible and edit, GeoAI, and upload actions are unavailable.</p>}{saveResult && <div className={saveResult.status === "REVIEW_REQUIRED" ? "saved-review" : "saved-success"} role="status"><span>Saved — Version {saveResult.version.version} created{saveResult.status === "REVIEW_REQUIRED" ? " · Review Required" : ""}{saveResult.issues.length ? `: ${saveResult.issues.map((issue) => `${issue.code} — ${issue.message}`).join(" ")}` : ""}</span><button type="button" aria-label="Dismiss save result" onClick={() => setSaveResult(null)}>×</button></div>}</div><div className="map-style-controls" aria-label="Basemap selection"><span>Basemap</span><button type="button" aria-pressed={basemapStyle === "STREET"} onClick={() => setBasemapStyle("STREET")}>Street</button><button type="button" aria-pressed={basemapStyle === "SATELLITE"} onClick={() => setBasemapStyle("SATELLITE")}>Satellite</button></div><div className="layer-controls" aria-label="Layer controls">{(Object.keys(layers) as Array<keyof LayerVisibility>).map((key) => <label key={key}><input aria-label={key === "landUse" ? "land use" : key} type="checkbox" checked={layers[key]} onChange={() => setLayers((current) => ({ ...current, [key]: !current[key] }))} /> {key === "landUse" ? "Land use" : key}</label>)}</div></header><SavedWebGisDemoPanel
      activeId={savedDemoId}
      loading={savedDemoSet.isLoading}
      error={savedDemoSet.isError}
      onSelect={(id) => {
        editor.cancel();
        drawer.cancel();
        setSelected(null);
        setSaveResult(null);
        setSavedDemoId(id);
        setSearchParams({ savedDemo: id });
      }}
      onClear={() => {
        editor.cancel();
        drawer.cancel();
        setSelected(null);
        setSavedDemoId(null);
        setSearchParams({});
      }}
    /><section className="gis-workspace"><div className="map-column">{!savedDemoId && !liveUnavailable && <ImageryGeoAiPanel projectId={projectId} assets={liveData.imagery} canUpload={canUploadImagery} canProcess={canProcessGeoAi} showLandUseComposition={layers.landUse} onChanged={refresh} onPreview={setImageryPreview} onZoomToImagery={requestImageryZoom} fullscreenHost={mapFullscreen ? mapStageRef.current : null} />}<div className="map-stage" ref={mapStageRef}>{mapFullscreen && <div className="fullscreen-map-controls" aria-label="Fullscreen map controls"><div className="fullscreen-layer-controls" aria-label="Fullscreen layer controls">{(Object.keys(layers) as Array<keyof LayerVisibility>).map((key) => <label key={key}><input aria-label={`fullscreen ${key === "landUse" ? "land use" : key}`} type="checkbox" checked={layers[key]} onChange={() => setLayers((current) => ({ ...current, [key]: !current[key] }))} /> {key === "landUse" ? "Land use" : key}</label>)}</div><div className="fullscreen-basemap-controls"><span>Map</span><button type="button" aria-pressed={basemapStyle === "STREET"} onClick={() => setBasemapStyle("STREET")}>Street</button><button type="button" aria-pressed={basemapStyle === "SATELLITE"} onClick={() => setBasemapStyle("SATELLITE")}>Satellite</button></div></div>}<button type="button" className="map-fullscreen-button" onClick={() => void toggleMapFullscreen()} aria-label={mapFullscreen ? "Exit map fullscreen" : "Open map fullscreen"}>{mapFullscreen ? "Exit fullscreen" : "Fullscreen"}</button><GisMap parcels={mapParcels} buildings={mapBuildings} roads={mapRoads} landUse={mapLandUse} topologyParcelIds={topologyParcelIds} visibility={layers} basemapStyle={basemapStyle} selectedParcelId={selected?.kind === "PARCEL" ? selected.id : null} onParcelSelect={(id) => { editor.cancel(); drawer.cancel(); setSaveResult(null); setSelected({ kind: "PARCEL", id }); setSearchParams(savedDemoId ? { savedDemo: savedDemoId, parcelId: id } : { parcelId: id }); }} onFeatureSelect={(kind, id) => { editor.cancel(); drawer.cancel(); setSaveResult(null); setSelected({ kind, id }); setSearchParams(savedDemoId ? { savedDemo: savedDemoId } : {}); }} editOverlay={editOverlay} drawOverlay={drawer.points !== null ? { points: drawer.points, onAddPoint: drawer.addPoint } : null} imageryPreview={mapPreview} imageryZoomRequest={imageryZoomRequest} />{empty && <div className="map-empty">{savedDemoId && savedDemoSet.isLoading ? "Loading preserved WebGIS demo…" : "No GIS features are available for this project yet."}</div>}<div className="map-legend"><strong>Map legend</strong>{layers.parcels && <span>Plots — yellow boundary/fill · blue building footprints · coral roads · white selected plot · red dashed topology issue</span>}{layers.landUse && <span>Land use — tree: dark green · shrubland: amber · grassland: yellow · cropland: pink · built-up: red · bare/sparse: gray · water: blue · wetland: teal · mangroves: green</span>}<span>AI plot candidates are non-statutory. Phase 12 edge evidence distinguishes road edges, visible linear imagery edges, vegetation edges, and geometry-only segments; generic visible edges are not claimed to be walls or fences. Cadastral/FMB or survey verification remains required.</span></div></div></div><div>{selected?.kind === "PARCEL" || !selected ? <ParcelPanel projectId={projectId} parcel={selectedParcel} issues={selectedIssues} editor={editor} canEdit={canEdit} onSaved={saved} demoMode={Boolean(savedDemo)} /> : <FeaturePanel kind={selected.kind} feature={selectedFeature} />}{!savedDemoId && <ParcelDrawPanel projectId={projectId} draw={drawer} canEdit={canEdit} onSaved={refresh} />}</div></section></main>;
}
