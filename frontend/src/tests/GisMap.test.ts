import { describe, expect, it, vi } from "vitest";

import { applyBasemapVisibility, applyOverlayVisibility, canApplyImagery, imageryLayerBeforeId, scheduleMapDataUpdate, selectedBoundaryEvidenceData } from "../components/GisMap";

function mapWithBasemapLayers() {
  return {
    getLayer: vi.fn(() => ({})),
    setLayoutProperty: vi.fn(),
  };
}

describe("applyBasemapVisibility", () => {
  it("shows Esri satellite and hides OSM street tiles when Satellite is selected", () => {
    const map = mapWithBasemapLayers();

    applyBasemapVisibility(map as never, "SATELLITE", true);

    expect(map.setLayoutProperty).toHaveBeenCalledWith("street-basemap", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("satellite-basemap", "visibility", "visible");
  });

  it("restores OSM street tiles when switching back to Street", () => {
    const map = mapWithBasemapLayers();

    applyBasemapVisibility(map as never, "SATELLITE", true);
    applyBasemapVisibility(map as never, "STREET", true);

    expect(map.setLayoutProperty.mock.calls.slice(-2)).toEqual([
      ["street-basemap", "visibility", "visible"],
      ["satellite-basemap", "visibility", "none"],
    ]);
  });

  it("hides both basemap layers when the global basemap checkbox is disabled", () => {
    const map = mapWithBasemapLayers();

    applyBasemapVisibility(map as never, "STREET", false);

    expect(map.setLayoutProperty).toHaveBeenCalledWith("street-basemap", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("satellite-basemap", "visibility", "none");
  });

  it("hides unchecked GIS overlay layers", () => {
    const map = {
      getLayer: vi.fn(() => ({})),
      setLayoutProperty: vi.fn(),
    };

    applyOverlayVisibility(map as never, {
      basemap: true,
      parcels: true,
      buildings: false,
      roads: false,
      landUse: false,
      topology: false,
    });

    expect(map.setLayoutProperty).toHaveBeenCalledWith("buildings-fill", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("buildings-outline", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("roads-line", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("land-use-fill", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("topology-outline", "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("parcels-fill", "visibility", "visible");
    expect(map.setLayoutProperty).toHaveBeenCalledWith("boundary-evidence-line", "visibility", "visible");
  });
});

describe("selectedBoundaryEvidenceData", () => {
  it("shows persisted edge evidence only for the selected parcel", () => {
    const parcels = [{
      id: "p1",
      current_version: {
        properties: {
          boundary_evidence: [{
            evidence_type: "ROAD_EDGE",
            confidence: 0.96,
            length_m: 12.5,
            support_fraction: 0.8,
            geometry: { type: "LineString", coordinates: [[78, 10], [78.001, 10.001]] },
          }],
        },
      },
    }] as never;

    const data = selectedBoundaryEvidenceData(parcels, "p1");

    expect(data.features).toHaveLength(1);
    expect(data.features[0].properties.evidence_type).toBe("ROAD_EDGE");
    expect(data.features[0].geometry.type).toBe("LineString");
    expect(selectedBoundaryEvidenceData(parcels, null).features).toHaveLength(0);
  });
});

describe("imageryLayerBeforeId", () => {
  it("places imagery below vector overlays once their anchor exists", () => {
    const map = { getLayer: vi.fn((id: string) => id === "land-use-fill" ? {} : undefined) };

    expect(imageryLayerBeforeId(map as never)).toBe("land-use-fill");
  });

  it("does not reference a missing overlay while the map load callback is still initializing layers", () => {
    const map = { getLayer: vi.fn(() => undefined) };

    expect(imageryLayerBeforeId(map as never)).toBeUndefined();
  });
});

describe("canApplyImagery", () => {
  it("updates an existing image source even while MapLibre reports the style as busy", () => {
    const map = {
      getLayer: vi.fn(() => undefined),
      getSource: vi.fn((id: string) => id === "imagery-preview" ? {} : undefined),
    };

    expect(canApplyImagery(map as never)).toBe(true);
  });

  it("applies after the application's vector layer graph is initialized", () => {
    const map = {
      getLayer: vi.fn((id: string) => id === "land-use-fill" ? {} : undefined),
      getSource: vi.fn(() => undefined),
    };

    expect(canApplyImagery(map as never)).toBe(true);
  });

  it("waits for the initial map load when neither application layers nor imagery exist", () => {
    const map = { getLayer: vi.fn(() => undefined), getSource: vi.fn(() => undefined) };

    expect(canApplyImagery(map as never)).toBe(false);
  });
});

describe("scheduleMapDataUpdate", () => {
  it("applies the latest GeoJSON data when MapLibre finishes loading instead of dropping the update", () => {
    const setData = vi.fn();
    let load: (() => void) | undefined;
    const map = {
      getLayer: vi.fn((id: string) => id === "parcels-line" ? {} : undefined),
      getSource: vi.fn(() => ({ setData })),
      isStyleLoaded: vi.fn(() => false),
      once: vi.fn((_event: string, listener: () => void) => { load = listener; }),
      off: vi.fn(),
      setPaintProperty: vi.fn(),
    };
    const latest = { type: "FeatureCollection", features: [{ type: "Feature", id: "active-building", geometry: { type: "Polygon", coordinates: [] }, properties: {} }] };

    const cleanup = scheduleMapDataUpdate(map as never, { buildings: latest } as never, null);

    expect(setData).not.toHaveBeenCalled();
    load?.();
    expect(setData).toHaveBeenCalledWith(latest);
    cleanup();
    expect(map.off).toHaveBeenCalledWith("load", load);
  });
});
