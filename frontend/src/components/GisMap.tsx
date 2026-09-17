import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Building, Geometry, LandUseFeature, Parcel, Road } from "../api/gis";
import type { PolygonGeometry, Position } from "../features/gis/geometry";

export interface LayerVisibility { basemap: boolean; parcels: boolean; buildings: boolean; roads: boolean; landUse: boolean; topology: boolean; }
export type MapFeatureKind = "BUILDING" | "ROAD" | "LAND_USE";
type MapFeature = { type: "Feature"; id: string; geometry: Geometry; properties: Record<string, unknown> };
type MapData = { type: "FeatureCollection"; features: MapFeature[] };
type MapItem = { id: string; geometry: Geometry; properties?: Record<string, unknown>; topology_issue?: boolean };
export interface EditOverlay { original: PolygonGeometry; working: PolygonGeometry; showOriginal: boolean; selectedVertex: number | null; onSelectVertex: (index: number) => void; onMoveVertex: (index: number, position: Position) => void; onCommitDrag: () => void; onAddVertex: (position: Position) => void; }

const collection = (features: MapFeature[]): MapData => ({ type: "FeatureCollection", features });
const feature = (item: MapItem): MapFeature => ({ type: "Feature", id: item.id, geometry: item.geometry, properties: { ...item, ...item.properties } });
const coordinatePairs = (coordinates: unknown): [number, number][] => {
  if (!Array.isArray(coordinates)) return [];
  if (coordinates.length >= 2 && typeof coordinates[0] === "number" && typeof coordinates[1] === "number") return [[coordinates[0], coordinates[1]]];
  return coordinates.flatMap(coordinatePairs);
};

export function GisMap({ parcels, buildings, roads, landUse, topologyParcelIds, visibility, selectedParcelId, onParcelSelect, onFeatureSelect, editOverlay }: { parcels: Parcel[]; buildings: Building[]; roads: Road[]; landUse: LandUseFeature[]; topologyParcelIds: string[]; visibility: LayerVisibility; selectedParcelId: string | null; onParcelSelect: (id: string) => void; onFeatureSelect: (kind: MapFeatureKind, id: string) => void; editOverlay: EditOverlay | null; }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<EditOverlay | null>(null); const draggingVertexRef = useRef<number | null>(null); overlayRef.current = editOverlay;
  const data = { parcels: collection(parcels.flatMap((parcel) => { const geometry = parcel.current_version.geometry; return geometry ? [feature({ ...parcel, geometry, topology_issue: topologyParcelIds.includes(parcel.id) })] : []; })), buildings: collection(buildings.map(feature)), roads: collection(roads.map(feature)), landUse: collection(landUse.map(feature)) };
  const editData = { original: collection(editOverlay?.showOriginal ? [feature({ id: "original", geometry: editOverlay.original })] : []), working: collection(editOverlay ? [feature({ id: "working", geometry: editOverlay.working })] : []), vertices: collection(editOverlay ? editOverlay.working.coordinates[0].slice(0, -1).map(([longitude, latitude], index) => feature({ id: `vertex-${index}`, geometry: { type: "Point", coordinates: [longitude, latitude] }, properties: { index, selected: editOverlay.selectedVertex === index } })) : []) };

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new maplibregl.Map({ container: container.current, center: [78.9629, 20.5937], zoom: 4, style: { version: 8, sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } }, layers: [{ id: "basemap", type: "raster", source: "osm" }] } });
    mapRef.current = map;
    map.on("load", () => {
      for (const [name, sourceData] of Object.entries({ ...data, ...editData })) map.addSource(name, { type: "geojson", data: sourceData as Parameters<maplibregl.GeoJSONSource["setData"]>[0] });
      const positions = Object.values(data).flatMap((sourceData) => sourceData.features.flatMap((mapFeature) => coordinatePairs(mapFeature.geometry.coordinates))).filter(([longitude, latitude]) => Number.isFinite(longitude) && Number.isFinite(latitude));
      if (positions.length) {
        const bounds = positions.reduce((result, position) => result.extend(position), new maplibregl.LngLatBounds(positions[0], positions[0]));
        map.fitBounds(bounds, { padding: 72, maxZoom: 18, duration: 0 });
      }
      map.addLayer({ id: "land-use-fill", type: "fill", source: "landUse", paint: { "fill-color": ["match", ["get", "land_use_class"], "WATERBODY", "#4e9ed4", "GREEN_OPEN_SPACE", "#68a357", "AGRICULTURAL", "#c8b560", "#d5a24b"], "fill-opacity": 0.32 } });
      map.addLayer({ id: "buildings-fill", type: "fill", source: "buildings", paint: { "fill-color": "#9a5b45", "fill-opacity": 0.55 } });
      map.addLayer({ id: "roads-line", type: "line", source: "roads", paint: { "line-color": "#3d4a55", "line-width": ["match", ["get", "road_class"], "ROAD", 4, "ACCESS_CORRIDOR", 3, 2] } });
      map.addLayer({ id: "parcels-fill", type: "fill", source: "parcels", paint: { "fill-color": ["case", ["==", ["get", "status"], "REVIEW_REQUIRED"], "#d1714a", "#c7a74b"], "fill-opacity": 0.22 } });
      map.addLayer({ id: "parcels-line", type: "line", source: "parcels", paint: { "line-color": ["case", ["==", ["get", "id"], selectedParcelId ?? ""], "#f7f3da", "#8f632f"], "line-width": ["case", ["==", ["get", "id"], selectedParcelId ?? ""], 4, 2] } });
      map.addLayer({ id: "parcel-labels", type: "symbol", source: "parcels", layout: { "text-field": ["coalesce", ["get", "external_identifier"], ""], "text-size": 12, "text-font": ["Open Sans Semibold"], "text-offset": [0, 0.8], "text-allow-overlap": false } });
      map.addLayer({ id: "topology-outline", type: "line", source: "parcels", filter: ["==", ["get", "topology_issue"], true], paint: { "line-color": "#bd4031", "line-width": 4, "line-dasharray": [1.5, 1.2] }, layout: { visibility: visibility.topology ? "visible" : "none" } });
      map.addLayer({ id: "edit-original-line", type: "line", source: "original", paint: { "line-color": "#5a6970", "line-width": 2, "line-dasharray": [2, 2] } });
      map.addLayer({ id: "edit-working-fill", type: "fill", source: "working", paint: { "fill-color": "#e86832", "fill-opacity": 0.18 } });
      map.addLayer({ id: "edit-working-line", type: "line", source: "working", paint: { "line-color": "#e86832", "line-width": 4 } });
      map.addLayer({ id: "edit-vertices", type: "circle", source: "vertices", paint: { "circle-radius": ["case", ["get", "selected"], 8, 6], "circle-color": ["case", ["get", "selected"], "#fff6db", "#173b47"], "circle-stroke-width": 2, "circle-stroke-color": "#e86832" } });
      map.on("click", "parcels-fill", (event) => { const id = event.features?.[0]?.properties?.id; if (typeof id === "string") onParcelSelect(id); });
      for (const [layerId, kind] of [["buildings-fill", "BUILDING"], ["roads-line", "ROAD"], ["land-use-fill", "LAND_USE"]] as const) map.on("click", layerId, (event) => { const id = event.features?.[0]?.properties?.id; if (typeof id === "string") onFeatureSelect(kind, id); });
      map.on("click", "edit-vertices", (event) => { event.preventDefault(); const index = event.features?.[0]?.properties?.index; if (typeof index === "number") overlayRef.current?.onSelectVertex(index); });
      map.on("click", "edit-working-line", (event) => { if (!event.defaultPrevented) overlayRef.current?.onAddVertex([event.lngLat.lng, event.lngLat.lat]); });
      map.on("mousedown", "edit-vertices", (event) => { const index = event.features?.[0]?.properties?.index; if (typeof index !== "number") return; draggingVertexRef.current = index; map.dragPan.disable(); event.preventDefault(); });
      map.on("mousemove", (event) => { const index = draggingVertexRef.current; if (index !== null) overlayRef.current?.onMoveVertex(index, [event.lngLat.lng, event.lngLat.lat]); });
      map.on("mouseup", () => { if (draggingVertexRef.current !== null) { overlayRef.current?.onCommitDrag(); draggingVertexRef.current = null; map.dragPan.enable(); } });
      map.on("mouseenter", "parcels-fill", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "parcels-fill", () => { map.getCanvas().style.cursor = ""; });
    });
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    const map = mapRef.current; if (!map?.isStyleLoaded()) return;
    for (const [name, sourceData] of Object.entries({ ...data, ...editData })) (map.getSource(name) as maplibregl.GeoJSONSource | undefined)?.setData(sourceData as Parameters<maplibregl.GeoJSONSource["setData"]>[0]);
    map.setPaintProperty("parcels-line", "line-color", ["case", ["==", ["get", "id"], selectedParcelId ?? ""], "#f7f3da", "#8f632f"]);
  }, [parcels, buildings, roads, landUse, topologyParcelIds, selectedParcelId, editOverlay]);
  useEffect(() => { const map = mapRef.current; if (!map?.isStyleLoaded()) return; const states: Record<string, boolean> = { basemap: visibility.basemap, "parcels-fill": visibility.parcels, "parcels-line": visibility.parcels, "parcel-labels": visibility.parcels, "buildings-fill": visibility.buildings, "roads-line": visibility.roads, "land-use-fill": visibility.landUse, "topology-outline": visibility.topology }; Object.entries(states).forEach(([id, shown]) => map.setLayoutProperty(id, "visibility", shown ? "visible" : "none")); }, [visibility]);

  return <div className="gis-map" ref={container} aria-label="Project cadastral map" data-testid="gis-map" data-parcel-count={data.parcels.features.length} data-building-count={data.buildings.features.length} />;
}
