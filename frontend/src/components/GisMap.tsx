import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Building, Geometry, LandUseFeature, Parcel, Road } from "../api/gis";
import type { PolygonGeometry, Position } from "../features/gis/geometry";

export interface LayerVisibility { basemap: boolean; parcels: boolean; buildings: boolean; roads: boolean; landUse: boolean; topology: boolean; }
export type BasemapStyle = "STREET" | "SATELLITE";
export type MapFeatureKind = "BUILDING" | "ROAD" | "LAND_USE";
type MapFeature = { type: "Feature"; id: string; geometry: Geometry; properties: Record<string, unknown> };
type MapData = { type: "FeatureCollection"; features: MapFeature[] };
type MapItem = { id: string; geometry: Geometry; properties?: Record<string, unknown>; topology_issue?: boolean };
export interface EditOverlay { original: PolygonGeometry; working: PolygonGeometry; showOriginal: boolean; selectedVertex: number | null; onSelectVertex: (index: number) => void; onMoveVertex: (index: number, position: Position) => void; onCommitDrag: () => void; onAddVertex: (position: Position) => void; }
export interface DrawOverlay { points: Position[]; onAddPoint: (position: Position) => void; }
export interface ImageryPreviewLayer { url: string; corners: [[number, number], [number, number], [number, number], [number, number]]; }

type BasemapMap = Pick<maplibregl.Map, "getLayer" | "setLayoutProperty">;
type OverlayMap = Pick<maplibregl.Map, "getLayer" | "setLayoutProperty">;
type LayerLookupMap = Pick<maplibregl.Map, "getLayer">;
type ImageryReadyMap = Pick<maplibregl.Map, "getLayer" | "getSource">;
type DataReadyMap = Pick<maplibregl.Map, "getLayer" | "getSource" | "isStyleLoaded" | "off" | "once" | "setPaintProperty">;

const collection = (features: MapFeature[]): MapData => ({ type: "FeatureCollection", features });
const feature = (item: MapItem): MapFeature => ({ type: "Feature", id: item.id, geometry: item.geometry, properties: { ...item, ...item.properties } });
const coordinatePairs = (coordinates: unknown): [number, number][] => {
  if (!Array.isArray(coordinates)) return [];
  if (coordinates.length >= 2 && typeof coordinates[0] === "number" && typeof coordinates[1] === "number") return [[coordinates[0], coordinates[1]]];
  return coordinates.flatMap(coordinatePairs);
};

export function selectedBoundaryEvidenceData(parcels: Parcel[], selectedParcelId: string | null): MapData {
  if (!selectedParcelId) return collection([]);
  const parcel = parcels.find((item) => item.id === selectedParcelId);
  const value = parcel?.current_version.properties?.boundary_evidence;
  if (!Array.isArray(value)) return collection([]);

  const features = value.flatMap((item, index): MapFeature[] => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const record = item as Record<string, unknown>;
    const geometry = record.geometry;
    if (!geometry || typeof geometry !== "object" || Array.isArray(geometry)) return [];
    const geometryRecord = geometry as Record<string, unknown>;
    if (geometryRecord.type !== "LineString" || !Array.isArray(geometryRecord.coordinates)) return [];
    return [{
      type: "Feature",
      id: `boundary-evidence-${index}`,
      geometry: geometry as Geometry,
      properties: {
        evidence_type: typeof record.evidence_type === "string" ? record.evidence_type : "GEOMETRY_ONLY",
        confidence: typeof record.confidence === "number" ? record.confidence : null,
        length_m: typeof record.length_m === "number" ? record.length_m : null,
        support_fraction: typeof record.support_fraction === "number" ? record.support_fraction : null,
      },
    }];
  });
  return collection(features);
}

export function applyBasemapVisibility(map: BasemapMap, basemapStyle: BasemapStyle, basemapEnabled: boolean): void {
  const visibility = (layer: BasemapStyle) => basemapEnabled && basemapStyle === layer ? "visible" : "none";
  const layers: Array<[string, BasemapStyle]> = [["street-basemap", "STREET"], ["satellite-basemap", "SATELLITE"]];
  for (const [layerId, style] of layers) {
    if (map.getLayer(layerId)) map.setLayoutProperty(layerId, "visibility", visibility(style));
  }
}

export function applyOverlayVisibility(map: OverlayMap, visibility: LayerVisibility): void {
  const states: Record<string, boolean> = {
    "parcels-fill": visibility.parcels,
    "parcels-casing": visibility.parcels,
    "parcels-line": visibility.parcels,
    "parcel-labels": visibility.parcels,
    "boundary-evidence-line": visibility.parcels,
    "buildings-fill": visibility.buildings,
    "buildings-casing": visibility.buildings,
    "buildings-outline": visibility.buildings,
    "roads-casing": visibility.roads,
    "roads-line": visibility.roads,
    "land-use-fill": visibility.landUse,
    "topology-outline": visibility.topology,
  };
  for (const [layerId, shown] of Object.entries(states)) {
    if (map.getLayer(layerId)) map.setLayoutProperty(layerId, "visibility", shown ? "visible" : "none");
  }
}

export function imageryLayerBeforeId(map: LayerLookupMap): string | undefined {
  return map.getLayer("land-use-fill") ? "land-use-fill" : undefined;
}

export function canApplyImagery(map: ImageryReadyMap): boolean {
  return Boolean(map.getLayer("land-use-fill") || map.getSource("imagery-preview"));
}

export function scheduleMapDataUpdate(map: DataReadyMap, sourceData: Record<string, MapData>, selectedParcelId: string | null): () => void {
  const apply = () => {
    for (const [name, data] of Object.entries(sourceData)) {
      (map.getSource(name) as maplibregl.GeoJSONSource | undefined)?.setData(data as Parameters<maplibregl.GeoJSONSource["setData"]>[0]);
    }
    if (map.getLayer("parcels-line")) {
      map.setPaintProperty("parcels-line", "line-color", ["case", ["==", ["get", "id"], selectedParcelId ?? ""], "#ffffff", "#f2c94c"]);
      map.setPaintProperty("parcels-line", "line-width", ["case", ["==", ["get", "id"], selectedParcelId ?? ""], 3.6, ["==", ["get", "source"], "AI_VISIBLE_BOUNDARY"], 2.2, 2]);
    }
  };
  if (map.isStyleLoaded()) apply();
  else map.once("load", apply);
  return () => map.off("load", apply);
}

export function GisMap({ parcels, buildings, roads, landUse, topologyParcelIds, visibility, basemapStyle, selectedParcelId, onParcelSelect, onFeatureSelect, editOverlay, drawOverlay, imageryPreview, imageryZoomRequest = 0 }: { parcels: Parcel[]; buildings: Building[]; roads: Road[]; landUse: LandUseFeature[]; topologyParcelIds: string[]; visibility: LayerVisibility; basemapStyle: BasemapStyle; selectedParcelId: string | null; onParcelSelect: (id: string) => void; onFeatureSelect: (kind: MapFeatureKind, id: string) => void; editOverlay: EditOverlay | null; drawOverlay?: DrawOverlay | null; imageryPreview?: ImageryPreviewLayer | null; imageryZoomRequest?: number; }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const overlayRef = useRef<EditOverlay | null>(null); const drawRef = useRef<DrawOverlay | null>(null); const draggingVertexRef = useRef<number | null>(null); overlayRef.current = editOverlay; drawRef.current = drawOverlay ?? null;
  const basemapRef = useRef({ style: basemapStyle, enabled: visibility.basemap });
  basemapRef.current = { style: basemapStyle, enabled: visibility.basemap };
  const data = { parcels: collection(parcels.flatMap((parcel) => { const geometry = parcel.current_version.geometry; return geometry ? [feature({ ...parcel, geometry, topology_issue: topologyParcelIds.includes(parcel.id) })] : []; })), buildings: collection(buildings.map(feature)), roads: collection(roads.map(feature)), landUse: collection(landUse.map(feature)), boundaryEvidence: selectedBoundaryEvidenceData(parcels, selectedParcelId) };
  const editData = { original: collection(editOverlay?.showOriginal ? [feature({ id: "original", geometry: editOverlay.original })] : []), working: collection(editOverlay ? [feature({ id: "working", geometry: editOverlay.working })] : []), vertices: collection(editOverlay ? editOverlay.working.coordinates[0].slice(0, -1).map(([longitude, latitude], index) => feature({ id: `vertex-${index}`, geometry: { type: "Point", coordinates: [longitude, latitude] }, properties: { index, selected: editOverlay.selectedVertex === index } })) : []), drawing: collection(drawOverlay && drawOverlay.points.length ? [feature({ id: "drawing", geometry: { type: drawOverlay.points.length >= 3 ? "Polygon" : "LineString", coordinates: drawOverlay.points.length >= 3 ? [[...drawOverlay.points, drawOverlay.points[0]]] : drawOverlay.points } })] : []), drawPoints: collection(drawOverlay?.points.map((point, index) => feature({ id: `draw-${index}`, geometry: { type: "Point", coordinates: point }, properties: { index } })) ?? []) };

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new maplibregl.Map({ container: container.current, center: [78.9629, 20.5937], zoom: 4, style: { version: 8, sources: { street: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "Â© OpenStreetMap contributors" }, satellite: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, attribution: "Â© Esri" } }, layers: [{ id: "street-basemap", type: "raster", source: "street", layout: { visibility: basemapStyle === "STREET" ? "visible" : "none" } }, { id: "satellite-basemap", type: "raster", source: "satellite", layout: { visibility: basemapStyle === "SATELLITE" ? "visible" : "none" } }] } });
    mapRef.current = map;
    resizeObserverRef.current = new ResizeObserver(() => map.resize());
    resizeObserverRef.current.observe(container.current);
    map.on("load", () => {
      applyBasemapVisibility(map, basemapRef.current.style, basemapRef.current.enabled);
      for (const [name, sourceData] of Object.entries({ ...data, ...editData })) map.addSource(name, { type: "geojson", data: sourceData as Parameters<maplibregl.GeoJSONSource["setData"]>[0] });
      const positions = Object.values(data).flatMap((sourceData) => sourceData.features.flatMap((mapFeature) => coordinatePairs(mapFeature.geometry.coordinates))).filter(([longitude, latitude]) => Number.isFinite(longitude) && Number.isFinite(latitude));
      if (positions.length) {
        const bounds = positions.reduce((result, position) => result.extend(position), new maplibregl.LngLatBounds(positions[0], positions[0]));
        map.fitBounds(bounds, { padding: 72, maxZoom: 18, duration: 0 });
      }
      map.addLayer({ id: "land-use-fill", type: "fill", source: "landUse", paint: { "fill-color": ["match", ["get", "land_use_class"], "TREE_COVER", "#006400", "SHRUBLAND", "#ffbb22", "GRASSLAND", "#ffff4c", "CROPLAND", "#f096ff", "BUILT_UP", "#fa0000", "BARE_SPARSE_VEGETATION", "#b4b4b4", "SNOW_ICE", "#f0f0f0", "PERMANENT_WATER_BODIES", "#0064c8", "HERBACEOUS_WETLAND", "#0096a0", "MANGROVES", "#00cf75", "MOSS_LICHEN", "#fae6a0", "WATERBODY", "#4e9ed4", "GREEN_OPEN_SPACE", "#68a357", "AGRICULTURAL", "#c8b560", "#d5a24b"], "fill-opacity": 0.38 } });
      map.addLayer({ id: "buildings-fill", type: "fill", source: "buildings", paint: { "fill-color": "#2f80ed", "fill-opacity": 0.24 } });
      map.addLayer({ id: "buildings-casing", type: "line", source: "buildings", paint: { "line-color": "#1f4e8c", "line-width": 3.2, "line-opacity": 0.72 } });
      map.addLayer({ id: "buildings-outline", type: "line", source: "buildings", paint: { "line-color": "#7fc8ff", "line-width": 1.35, "line-opacity": 0.98 } });
      map.addLayer({ id: "roads-casing", type: "line", source: "roads", paint: { "line-color": "#5b2520", "line-width": 4.4, "line-opacity": 0.58 } });
      map.addLayer({
        id: "roads-line",
        type: "line",
        source: "roads",
        paint: {
          "line-color": [
            "match",
            ["get", "model_version"],
            "sam-road-cityscale-vitb", "#e45b3e",
            "sam-road-spacenet-vitb", "#e45b3e",
            "sam-road-spacenet-vitb-centerline-v1", "#e45b3e",
            "sam-road-spacenet-vitb-centerline-v2", "#e45b3e",
            "sam-road-spacenet-vitb-centerline-v3", "#e45b3e",
            "sam-road-spacenet-vitb-centerline-v4", "#e45b3e",
            "sam-road-spacenet-vitb-centerline-v5", "#e45b3e",
            "sam-road-spacenet-vitb-centerline-v6", "#e45b3e",
            "sam-road-spacenet-vitb-centerline-v7", "#e45b3e",
            "#d65a42"
          ],
          "line-width": [
            "match",
            ["get", "model_version"],
            "sam-road-cityscale-vitb", 2.6,
            "sam-road-spacenet-vitb", 2.6,
            "sam-road-spacenet-vitb-centerline-v1", 2.6,
            "sam-road-spacenet-vitb-centerline-v2", 2.6,
            "sam-road-spacenet-vitb-centerline-v3", 2.6,
            "sam-road-spacenet-vitb-centerline-v4", 2.6,
            "sam-road-spacenet-vitb-centerline-v5", 2.6,
            "sam-road-spacenet-vitb-centerline-v6", 2.6,
            "sam-road-spacenet-vitb-centerline-v7", 2.6,
            ["match", ["get", "road_class"], "ROAD", 2.5, "ACCESS_CORRIDOR", 2, 1.6]
          ],
          "line-opacity": 0.94
        }
      });
      map.addLayer({ id: "parcels-fill", type: "fill", source: "parcels", paint: { "fill-color": "#f2c94c", "fill-opacity": 0.10 } });
      map.addLayer({ id: "parcels-casing", type: "line", source: "parcels", paint: { "line-color": "#102a43", "line-width": ["case", ["==", ["get", "id"], selectedParcelId ?? ""], 5, 4], "line-opacity": 0.46 } });
      map.addLayer({ id: "parcels-line", type: "line", source: "parcels", paint: { "line-color": ["case", ["==", ["get", "id"], selectedParcelId ?? ""], "#ffffff", "#f2c94c"], "line-width": ["case", ["==", ["get", "id"], selectedParcelId ?? ""], 3.6, 2.2] } });
      map.addLayer({
        id: "boundary-evidence-line",
        type: "line",
        source: "boundaryEvidence",
        paint: {
          "line-color": ["match", ["get", "evidence_type"], "ROAD_EDGE", "#ff6b4a", "VISIBLE_LINEAR_EDGE", "#ffd166", "VEGETATION_EDGE", "#57cc99", "#f5f5f5"],
          "line-width": ["match", ["get", "evidence_type"], "ROAD_EDGE", 4.8, "VISIBLE_LINEAR_EDGE", 4, "VEGETATION_EDGE", 4, 2.6],
          "line-opacity": 0.98,
        },
      });
      map.addLayer({ id: "parcel-labels", type: "symbol", source: "parcels", layout: { "text-field": ["coalesce", ["get", "external_identifier"], "Plot"], "text-size": 11.5, "text-font": ["Open Sans Semibold"], "text-letter-spacing": 0.02, "text-padding": 4, "text-offset": [0, 0], "text-allow-overlap": false }, paint: { "text-color": "#ffffff", "text-halo-color": "rgba(17,24,39,0.94)", "text-halo-width": 3.6, "text-halo-blur": 0.15 } });
      map.addLayer({ id: "topology-outline", type: "line", source: "parcels", filter: ["==", ["get", "topology_issue"], true], paint: { "line-color": "#bd4031", "line-width": 4, "line-dasharray": [1.5, 1.2] }, layout: { visibility: visibility.topology ? "visible" : "none" } });
      map.addLayer({ id: "drawing-line", type: "line", source: "drawing", paint: { "line-color": "#174c5b", "line-width": 3, "line-dasharray": [2, 1] } });
      map.addLayer({ id: "drawing-fill", type: "fill", source: "drawing", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#38a1a5", "fill-opacity": 0.18 } });
      map.addLayer({ id: "drawing-points", type: "circle", source: "drawPoints", paint: { "circle-radius": 5, "circle-color": "#fffdf8", "circle-stroke-width": 2, "circle-stroke-color": "#174c5b" } });
      map.addLayer({ id: "edit-original-line", type: "line", source: "original", paint: { "line-color": "#5a6970", "line-width": 2, "line-dasharray": [2, 2] } });
      map.addLayer({ id: "edit-working-fill", type: "fill", source: "working", paint: { "fill-color": "#e86832", "fill-opacity": 0.18 } });
      map.addLayer({ id: "edit-working-line", type: "line", source: "working", paint: { "line-color": "#e86832", "line-width": 4 } });
      map.addLayer({ id: "edit-vertices", type: "circle", source: "vertices", paint: { "circle-radius": ["case", ["get", "selected"], 8, 6], "circle-color": ["case", ["get", "selected"], "#fff6db", "#173b47"], "circle-stroke-width": 2, "circle-stroke-color": "#e86832" } });
      applyOverlayVisibility(map, visibility);
      map.on("click", "parcels-fill", (event) => { const id = event.features?.[0]?.properties?.id; if (typeof id === "string") onParcelSelect(id); });
      for (const [layerId, kind] of [["buildings-fill", "BUILDING"], ["roads-line", "ROAD"], ["land-use-fill", "LAND_USE"]] as const) map.on("click", layerId, (event) => { const id = event.features?.[0]?.properties?.id; if (typeof id === "string") onFeatureSelect(kind, id); });
      map.on("click", "edit-vertices", (event) => { event.preventDefault(); const index = event.features?.[0]?.properties?.index; if (typeof index === "number") overlayRef.current?.onSelectVertex(index); });
      map.on("click", "edit-working-line", (event) => { if (!event.defaultPrevented) overlayRef.current?.onAddVertex([event.lngLat.lng, event.lngLat.lat]); });
      map.on("click", (event) => { if (drawRef.current) drawRef.current.onAddPoint([event.lngLat.lng, event.lngLat.lat]); });
      map.on("mousedown", "edit-vertices", (event) => { const index = event.features?.[0]?.properties?.index; if (typeof index !== "number") return; draggingVertexRef.current = index; map.dragPan.disable(); event.preventDefault(); });
      map.on("mousemove", (event) => { const index = draggingVertexRef.current; if (index !== null) overlayRef.current?.onMoveVertex(index, [event.lngLat.lng, event.lngLat.lat]); });
      map.on("mouseup", () => { if (draggingVertexRef.current !== null) { overlayRef.current?.onCommitDrag(); draggingVertexRef.current = null; map.dragPan.enable(); } });
      map.on("mouseenter", "parcels-fill", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "parcels-fill", () => { map.getCanvas().style.cursor = ""; });
    });
    return () => { resizeObserverRef.current?.disconnect(); resizeObserverRef.current = null; map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    return scheduleMapDataUpdate(map, { ...data, ...editData }, selectedParcelId);
  }, [parcels, buildings, roads, landUse, topologyParcelIds, selectedParcelId, editOverlay, drawOverlay]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    applyBasemapVisibility(map, basemapStyle, visibility.basemap);
  }, [basemapStyle, visibility.basemap]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    applyOverlayVisibility(map, visibility);
  }, [visibility]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const applyImagery = () => {
      const source = map.getSource("imagery-preview") as maplibregl.ImageSource | undefined;
      const beforeId = imageryLayerBeforeId(map);

      if (imageryPreview) {
        if (source) {
          source.updateImage({
            url: imageryPreview.url,
            coordinates: imageryPreview.corners,
          });
        } else {
          map.addSource("imagery-preview", {
            type: "image",
            url: imageryPreview.url,
            coordinates: imageryPreview.corners,
          });

          map.addLayer(
            {
              id: "imagery-preview",
              type: "raster",
              source: "imagery-preview",
              paint: { "raster-opacity": 1.0 },
            },
            beforeId,
          );
        }

        const bounds = imageryPreview.corners.reduce(
          (result, corner) => result.extend(corner),
          new maplibregl.LngLatBounds(
            imageryPreview.corners[0],
            imageryPreview.corners[0],
          ),
        );

        map.fitBounds(bounds, {
          padding: 48,
          maxZoom: 19,
          duration: 700,
        });
      } else {
        if (map.getLayer("imagery-preview")) {
          map.removeLayer("imagery-preview");
        }
        if (map.getSource("imagery-preview")) {
          map.removeSource("imagery-preview");
        }

      }
    };

    if (canApplyImagery(map)) {
      applyImagery();
    } else {
      map.once("load", applyImagery);
    }

    return () => {
      map.off("load", applyImagery);
    };
  }, [imageryPreview]);

  useEffect(() => {
    if (!imageryPreview || imageryZoomRequest <= 0) return;

    const map = mapRef.current;
    if (!map) return;

    const bounds = imageryPreview.corners.reduce(
      (result, corner) => result.extend(corner),
      new maplibregl.LngLatBounds(
        imageryPreview.corners[0],
        imageryPreview.corners[0],
      ),
    );

    map.stop();
    map.fitBounds(bounds, {
      padding: 48,
      maxZoom: 17,
      duration: 500,
    });
  }, [imageryZoomRequest, imageryPreview]);
  return <div className="gis-map" ref={container} aria-label="Project cadastral map" data-testid="gis-map" data-parcel-count={data.parcels.features.length} data-building-count={data.buildings.features.length} />;
}

