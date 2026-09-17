import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError, loadCurrentUser, loadGisProject, loadParcelVersions, type GeoFeature, type Parcel, type ParcelVersionSaveResult, type TopologyError } from "../api/gis";
import { GisMap, type LayerVisibility, type MapFeatureKind } from "../components/GisMap";
import { ParcelEditor } from "../components/ParcelEditor";
import { useParcelEditor } from "../features/gis/useParcelEditor";

const initialLayers: LayerVisibility = { basemap: true, parcels: true, buildings: true, roads: true, landUse: true, topology: true };
const measure = (value: number | null) => value === null ? "Not available" : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
const layerStorageKey = (projectId: string) => `gis-layer-visibility:${projectId}`;

function readLayerVisibility(projectId: string): LayerVisibility {
  try {
    const stored = localStorage.getItem(layerStorageKey(projectId));
    if (!stored) return initialLayers;
    return { ...initialLayers, ...(JSON.parse(stored) as Partial<LayerVisibility>) };
  } catch {
    return initialLayers;
  }
}

function ParcelPanel({ projectId, parcel, issues, editor, canEdit, onSaved }: { projectId: string; parcel: Parcel | null; issues: TopologyError[]; editor: ReturnType<typeof useParcelEditor>; canEdit: boolean; onSaved: (result: ParcelVersionSaveResult) => void }) {
  const versions = useQuery({ queryKey: ["parcel-versions", projectId, parcel?.id], queryFn: () => loadParcelVersions(projectId, parcel!.id), enabled: Boolean(parcel) });
  if (!parcel) return <aside className="detail-panel empty-panel"><h2>Feature details</h2><p>Select a parcel boundary to inspect its draft metadata. Building footprints remain separate from parcel boundaries.</p></aside>;
  const version = parcel.current_version;
  return <aside className="detail-panel" aria-label="Parcel details"><p className="eyebrow">Draft parcel</p><h2>{parcel.external_identifier ?? parcel.id}</h2><div className="status-row"><span>{parcel.status}</span><span>{parcel.verification_status}</span>{parcel.ai_boundary_status && <span>{parcel.ai_boundary_status}</span>}</div><dl><dt>Parcel ID</dt><dd>{parcel.id}</dd><dt>Source</dt><dd>{parcel.source}</dd><dt>Source reference</dt><dd>{parcel.source_reference ?? "Not supplied"}</dd><dt>Current version</dt><dd>{parcel.current_geometry_version}</dd><dt>Source CRS</dt><dd>{parcel.source_crs ?? "No declared CRS"}</dd><dt>Processed at</dt><dd>{version.processed_at ? new Date(version.processed_at).toLocaleString() : "Not supplied"}</dd><dt>Confidence</dt><dd>{parcel.confidence === null ? "Not available" : `${Math.round(parcel.confidence * 100)}%`}</dd><dt>Model version</dt><dd>{parcel.model_version ?? "Not applicable"}</dd><dt>Area</dt><dd>{measure(version.area_m2)} m²</dd><dt>Area</dt><dd>{measure(version.area_sqft)} sq ft</dd><dt>Survey required</dt><dd>{parcel.requires_survey ? "Yes" : "No"}</dd><dt>Topology issues</dt><dd><strong className={issues.length ? "issue-count" : ""}>{issues.length}</strong></dd></dl>{issues.length > 0 && <section className="topology-list" aria-label="Unresolved topology issues"><h3>Unresolved topology findings</h3>{issues.map((issue) => <article key={issue.id}><strong>{issue.severity} · {issue.code}</strong><span>{issue.message}</span>{issue.area_m2 !== null && <span>{measure(issue.area_m2)} m² affected</span>}</article>)}</section>}<ParcelEditor projectId={projectId} parcel={parcel} editor={editor} canEdit={canEdit} onSaved={onSaved} /><h3>Version history</h3>{versions.isLoading && <p>Loading versions…</p>}{versions.isError && <p className="error-copy">Version history is unavailable. Refresh the GIS data and try again.</p>}{versions.data && <ol className="version-list">{versions.data.map((item) => <li key={item.id}><strong>v{item.version}</strong><span>{item.created_by_type} · {item.created_by_user_id ?? "system"} · {new Date(item.created_at).toLocaleDateString()}</span><span>{measure(item.area_m2)} m² · {measure(item.area_sqft)} sq ft</span><span>{item.source} · {item.source_reference ?? "No source reference"}</span>{item.change_reason && <span>{item.change_reason}</span>}</li>)}</ol>}<p className="panel-note">Draft geometry remains provisional and requires authorized verification. Saving appends a new immutable version.</p></aside>;
}

function FeaturePanel({ kind, feature }: { kind: MapFeatureKind; feature: GeoFeature | null }) {
  if (!feature) return <aside className="detail-panel empty-panel"><h2>Feature details</h2><p>The selected GIS feature is no longer available. Refresh the map or select another feature.</p></aside>;
  const title = kind === "ROAD" ? String(feature.properties.road_class ?? "Road or pathway") : kind === "LAND_USE" ? String(feature.properties.land_use_class ?? "Land use") : "Building footprint";
  return <aside className="detail-panel" aria-label={`${kind.toLowerCase()} details`}><p className="eyebrow">{kind.replace("_", " ")}</p><h2>{title}</h2><div className="status-row"><span>{feature.status}</span><span>{feature.verification_status}</span></div><dl><dt>Feature ID</dt><dd>{feature.id}</dd><dt>Source</dt><dd>{feature.source}</dd><dt>Source reference</dt><dd>{feature.source_reference ?? "Not supplied"}</dd><dt>Confidence</dt><dd>{feature.confidence === null ? "Not available" : `${Math.round(feature.confidence * 100)}%`}</dd><dt>Model version</dt><dd>{feature.model_version ?? "Not applicable"}</dd><dt>Processed at</dt><dd>{feature.processed_at ? new Date(feature.processed_at).toLocaleString() : "Not supplied"}</dd>{kind === "ROAD" && <><dt>Length</dt><dd>{measure(feature.properties.length_m as number | null)} m</dd></>}{kind !== "ROAD" && <><dt>Area</dt><dd>{measure(feature.properties.area_m2 as number | null)} m²</dd><dt>Area</dt><dd>{measure(feature.properties.area_sqft as number | null)} sq ft</dd></>}</dl><p className="panel-note">Read-only GIS context. This feature is not a legal parcel determination.</p></aside>;
}

export function GisPage() {
  const { projectId } = useParams();
  const queryClient = useQueryClient();
  const editor = useParcelEditor();
  const [layers, setLayers] = useState<LayerVisibility>(() => projectId ? readLayerVisibility(projectId) : initialLayers);
  const [selected, setSelected] = useState<{ kind: "PARCEL" | MapFeatureKind; id: string } | null>(null);
  const [saveResult, setSaveResult] = useState<ParcelVersionSaveResult | null>(null);

  useEffect(() => {
    if (projectId) setLayers(readLayerVisibility(projectId));
  }, [projectId]);
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

  if (!projectId) return <main className="gis-state"><h1>Project GIS unavailable</h1></main>;
  if (query.isLoading) return <main className="gis-state"><p className="eyebrow">Web-GIS</p><h1>Loading project layers…</h1></main>;
  if (query.isError && !query.data) { const error = query.error as ApiError; const message = error.status === 403 ? "You do not have permission to view this project’s GIS layers." : error.status === 401 ? "Sign in with a session that has geo:read permission." : error.status === 404 ? "This project or its GIS data was not found." : "The GIS layers could not be loaded."; return <main className="gis-state"><p className="eyebrow">Web-GIS</p><h1>Map unavailable</h1><p>{message}</p><button type="button" onClick={() => query.refetch()}>Retry</button> <Link to="/">Return to platform</Link></main>; }

  const data = query.data!;
  const selectedParcel = selected?.kind === "PARCEL" ? data.parcels.find((parcel) => parcel.id === selected.id) ?? null : null;
  const selectedFeature = selected?.kind === "BUILDING" ? data.buildings.find((item) => item.id === selected.id) ?? null : selected?.kind === "ROAD" ? data.roads.find((item) => item.id === selected.id) ?? null : selected?.kind === "LAND_USE" ? data.landUse.find((item) => item.id === selected.id) ?? null : null;
  const selectedIssues = selectedParcel ? data.topology.filter((issue) => issue.parcel_id === selectedParcel.id || issue.related_parcel_id === selectedParcel.id) : [];
  const empty = !data.parcels.length && !data.buildings.length && !data.roads.length && !data.landUse.length;
  const topologyParcelIds = [...new Set(data.topology.flatMap((issue) => [issue.parcel_id, issue.related_parcel_id]).filter((id): id is string => Boolean(id)))];
  const membership = currentUser.data?.project_memberships?.some((item) => item.project_id === projectId) ?? false;
  const canEdit = Boolean(currentUser.data?.permissions?.includes("geo:edit_draft") && membership);
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
    await query.refetch();
    if (selectedParcel) await queryClient.invalidateQueries({ queryKey: ["parcel-versions", projectId, selectedParcel.id] });
  };

  return <main className="gis-shell"><header className="gis-header"><div><p className="eyebrow">Project Web-GIS · D.3</p><h1>Project cadastral viewer</h1><p>Project: {projectId}</p><div className="sync-row"><button type="button" onClick={refresh} disabled={query.isFetching || Boolean(editor.session)}>{query.isFetching ? "Refreshing…" : "Refresh GIS data"}</button><span>{editor.session ? "Automatic refresh paused while editing" : lastSyncedAt ? `Last synced ${lastSyncedAt.toLocaleTimeString()}` : "Waiting for first sync"}</span></div>{query.isError && query.data && <p className="sync-warning" role="status">Latest refresh failed. Showing the most recently loaded GIS data.</p>}{currentUser.isError && <p className="sync-warning" role="status">Editing permissions could not be verified. The map remains read-only until the user session can be checked.</p>}{saveResult && <div className={saveResult.status === "REVIEW_REQUIRED" ? "saved-review" : "saved-success"} role="status"><span>Saved — Version {saveResult.version.version} created{saveResult.status === "REVIEW_REQUIRED" ? " · Review Required" : ""}{saveResult.issues.length ? `: ${saveResult.issues.map((issue) => `${issue.code} — ${issue.message}`).join(" ")}` : ""}</span><button type="button" aria-label="Dismiss save result" onClick={() => setSaveResult(null)}>×</button></div>}</div><div className="layer-controls" aria-label="Layer controls">{(Object.keys(layers) as Array<keyof LayerVisibility>).map((key) => <label key={key}><input aria-label={key === "landUse" ? "land use" : key} type="checkbox" checked={layers[key]} onChange={() => setLayers((current) => ({ ...current, [key]: !current[key] }))} /> {key === "landUse" ? "Land use" : key}</label>)}</div></header><section className="gis-workspace"><div className="map-column"><GisMap parcels={data.parcels} buildings={data.buildings} roads={data.roads} landUse={data.landUse} topologyParcelIds={topologyParcelIds} visibility={layers} selectedParcelId={selected?.kind === "PARCEL" ? selected.id : null} onParcelSelect={(id) => { editor.cancel(); setSaveResult(null); setSelected({ kind: "PARCEL", id }); }} onFeatureSelect={(kind, id) => { editor.cancel(); setSaveResult(null); setSelected({ kind, id }); }} editOverlay={editOverlay} />{empty && <div className="map-empty">No GIS features are available for this project yet.</div>}<div className="map-legend"><strong>Land use</strong><span>Residential/commercial/industrial: amber · Agricultural: ochre · Water: blue · Green: green · Unknown: neutral</span><span>Parcels are draft/preliminary. Buildings are not parcel boundaries. Red dashes flag linked unresolved topology findings.</span></div></div>{selected?.kind === "PARCEL" || !selected ? <ParcelPanel projectId={projectId} parcel={selectedParcel} issues={selectedIssues} editor={editor} canEdit={canEdit} onSaved={saved} /> : <FeaturePanel kind={selected.kind} feature={selectedFeature} />}</section></main>;
}
