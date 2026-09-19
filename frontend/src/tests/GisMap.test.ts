import { describe, expect, it, vi } from "vitest";

import { applyBasemapVisibility } from "../components/GisMap";

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
});
