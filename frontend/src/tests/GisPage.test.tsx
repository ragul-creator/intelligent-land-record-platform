import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GisPage, buildingsForActiveImagery, landUseForActiveImagery, parcelsForActiveImagery } from "../pages/GisPage";
import { geoAiCompletionMessage } from "../components/ImageryGeoAiPanel";
import type { GeoFeature, Parcel } from "../api/gis";

vi.mock("../components/GisMap", () => ({
  GisMap: ({ parcels, buildings, roads, landUse, topologyParcelIds, onParcelSelect, onFeatureSelect }: { parcels: Array<{ id: string }>; buildings: unknown[]; roads: Array<{ id: string }>; landUse: unknown[]; topologyParcelIds: string[]; onParcelSelect: (id: string) => void; onFeatureSelect: (kind: "ROAD", id: string) => void }) => (
    <div data-testid="gis-map" data-parcel-count={parcels.length} data-building-count={buildings.length} data-road-count={roads.length} data-land-use-count={landUse.length} data-topology-count={topologyParcelIds.length}>
      {parcels[0] && <button type="button" onClick={() => onParcelSelect(parcels[0].id)}>Select parcel</button>}
      {roads[0] && <button type="button" onClick={() => onFeatureSelect("ROAD", roads[0].id)}>Select road</button>}
    </div>
  ),
}));

const page = (items: unknown[]) => ({ items, page: { limit: 500, offset: 0, total: items.length } });
const parcel: Parcel = {
  id: "parcel-1", project_id: "project-1", external_identifier: "Draft parcel A", source: "EXISTING_GIS", source_reference: "survey-2026", status: "DRAFT", verification_status: "UNVERIFIED", current_geometry_version: 1, coordinate_space: "WORLD", source_crs: "EPSG:32643", confidence: 0.82, model_version: null, ai_boundary_status: "NOT_DETERMINED", requires_survey: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  current_version: { id: "version-1", version: 1, geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] }, source: "EXISTING_GIS", source_reference: "survey-2026", coordinate_space: "WORLD", source_crs: "EPSG:32643", area_m2: 120, area_sqft: 1291.67, change_reason: null, validation_status: "VALID", created_by_user_id: null, created_by_type: "IMPORT", processed_at: "2026-01-01T00:00:00Z", created_at: "2026-01-01T00:00:00Z" },
};
const notDeterminedParcel = { ...parcel, id: "parcel-no-geometry", external_identifier: "Not determined", ai_boundary_status: "NOT_DETERMINED", current_version: { ...parcel.current_version, id: "version-no-geometry", geometry: null } };
const currentUser = { id: "user-1", login_id: "SUR-TN-1", email: "surveyor@example.invalid", full_name: "Surveyor", roles: ["SURVEYOR"], permissions: ["geo:read", "geo:edit_draft", "imagery:upload", "geoai:process"], project_memberships: [{ project_id: "project-1", role: "SURVEYOR" }] };
const topologyIssue = { id: "topology-1", project_id: "project-1", parcel_id: "parcel-1", related_parcel_id: null, code: "NEIGHBOUR_OVERLAP", severity: "REVIEW", area_m2: 4.25, message: "Edited parcel overlaps a neighbour.", resolved: false, created_at: "2026-01-01T00:00:00Z" };

function installProjectFetch(imageryAssets: unknown[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/demo/webgis-demo-result.json")) {
      const demoParcel = {
        ...parcel,
        id: "saved-parcel-1",
        external_identifier: "P-1001",
        source: "AI_VISIBLE_BOUNDARY",
        source_reference: "imagery:saved-vegas:parcel-candidate",
        current_version: { ...parcel.current_version, id: "saved-version-1", source: "AI_VISIBLE_BOUNDARY", source_reference: "imagery:saved-vegas:parcel-candidate" },
      };
      return new Response(JSON.stringify({
        project_name: "land-parcelling",
        captured_at: "2026-09-29T14:07:42Z",
        display_note: "Preserved run",
        demos: [
          {
            id: "vegas-geoai",
            title: "Vegas building, road & plot demo",
            filename: "vegas_building_test.tif",
            preview_url: "/demo/webgis-vegas-preview.png",
            source_url: "/demo/webgis-vegas-source.tif",
            imagery_asset_id: "saved-vegas",
            corners_wgs84: [[-115.2, 36.1], [-115.1, 36.1], [-115.1, 36.0], [-115.2, 36.0]],
            metadata: { width: 650, height: 650 },
            summary: { buildings: 29, roads: 9, parcels: 42 },
            jobs: [],
            buildings: Array.from({ length: 29 }, (_, index) => ({ id: `saved-building-${index}`, project_id: "project-1", geometry: { type: "Polygon", coordinates: [] }, source: "AI_CANDIDATE", source_reference: "imagery:saved-vegas", confidence: .9, model_version: "building-segmentation-c2-mixed-v5-hardneg", status: "AI_PRELIMINARY", verification_status: "UNVERIFIED", processed_at: null, properties: {} })),
            roads: Array.from({ length: 9 }, (_, index) => ({ id: `saved-road-${index}`, project_id: "project-1", geometry: { type: "LineString", coordinates: [] }, source: "AI_CANDIDATE", source_reference: "imagery:saved-vegas", confidence: .9, model_version: "sam-road-spacenet-vitb-centerline-v7", status: "AI_PRELIMINARY", verification_status: "UNVERIFIED", processed_at: null, properties: { road_class: "ROAD" } })),
            parcels: Array.from({ length: 42 }, (_, index) => ({ ...demoParcel, id: `saved-parcel-${index}`, external_identifier: `P-${1001 + index}` })),
            landUse: [],
            topology: [],
          },
          {
            id: "tamilnadu-lulc",
            title: "Tamil Nadu RGB+NIR land-use demo",
            filename: "lulc.tif",
            preview_url: "/demo/webgis-lulc-preview.png",
            source_url: null,
            imagery_asset_id: "saved-lulc",
            corners_wgs84: [[80, 10], [81, 10], [81, 9], [80, 9]],
            metadata: {},
            summary: { land_use_regions: 9 },
            jobs: [],
            buildings: [], roads: [], parcels: [],
            landUse: Array.from({ length: 9 }, (_, index) => ({ id: `saved-lulc-${index}`, project_id: "project-1", geometry: { type: "MultiPolygon", coordinates: [] }, source: "AI_CANDIDATE", source_reference: "imagery:saved-lulc", confidence: .8, model_version: "segformer-b2-worldcover-2021-tamilnadu-v1", status: "AI_PRELIMINARY", verification_status: "UNVERIFIED", processed_at: null, properties: { land_use_class: "CROPLAND" } })),
            topology: [],
          },
        ],
      }), { status: 200 });
    }
    if (url.includes("/users/me")) return new Response(JSON.stringify(currentUser), { status: 200 });
    if (url.includes("/versions")) return new Response(JSON.stringify(page([parcel.current_version])), { status: 200 });
    if (url.includes("/record-parcel-links")) return new Response(JSON.stringify(page([{ id: "link-1", project_id: "project-1", document_id: "doc-1", document_validation_result_id: "validation-1", parcel_id: "parcel-1", parcel_display_identifier: "Draft parcel A", link_status: "CONFIRMED", link_method: "EXACT_SURVEY_IDENTIFIER", confidence: 0.97, rationale: {}, provenance: {}, review_required: false, review_task_id: null, review_reason: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }])), { status: 200 });
    if (url.includes("/parcels?")) return new Response(JSON.stringify(page([parcel, notDeterminedParcel])), { status: 200 });
    if (url.includes("/buildings")) return new Response(JSON.stringify(page([{ id: "building-1" }])), { status: 200 });
    if (url.includes("/roads")) return new Response(JSON.stringify(page([{ id: "road-1", source: "EXISTING_GIS", source_reference: "road-survey", confidence: 0.9, model_version: null, status: "DRAFT", verification_status: "UNVERIFIED", processed_at: null, geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] }, properties: { road_class: "ROAD", length_m: 155.5 } }])), { status: 200 });
    if (url.includes("/land-use")) return new Response(JSON.stringify(page([{ id: "land-use-1" }])), { status: 200 });
    if (url.includes("/topology-errors")) return new Response(JSON.stringify(page([topologyIssue])), { status: 200 });
    if (url.includes("/preview-url")) return new Response(JSON.stringify({ imagery_asset_id: "imagery-1", preview_url: "https://example.invalid/preview.png", corners_wgs84: [[0, 1], [1, 1], [1, 0], [0, 0]], expires_in_seconds: 900 }), { status: 200 });
    if (url.includes("/imagery")) return new Response(JSON.stringify(page(imageryAssets)), { status: 200 });
    if (url.includes("/geoai/jobs")) return new Response(JSON.stringify({ id: "road-job-1", project_id: "project-1", job_type: "ROAD_VECTORIZE", status: "QUEUED", progress: 0, has_error: false, output_references: {}, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }), { status: 202 });
    return new Response(JSON.stringify(page([])), { status: 200 });
  }));
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={["/projects/project-1/gis"]}><Routes><Route path="/projects/:projectId/gis" element={<GisPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

describe("GisPage", () => {
  it("describes valid Road and land-use GeoAI results explicitly", () => {
    expect(geoAiCompletionMessage("ROAD_VECTORIZE", { feature_count: 0 }, true)).toBe(
      "Road processing completed — No road candidates detected. Imagery and map layers refreshed.",
    );
    expect(geoAiCompletionMessage("ROAD_VECTORIZE", { feature_count: 9 }, true)).toBe(
      "Road processing completed — 9 road candidates detected. Imagery and map layers refreshed.",
    );
    expect(geoAiCompletionMessage("LAND_USE_VECTORIZE", { feature_count: 0 }, true)).toBe(
      "Land-use processing completed — No land-use regions detected. Imagery and map layers refreshed.",
    );
    expect(geoAiCompletionMessage("LAND_USE_VECTORIZE", { feature_count: 8 }, true)).toBe(
      "Land-use processing completed — 8 land-use class regions detected. Imagery and map layers refreshed.",
    );
    expect(geoAiCompletionMessage("PARCEL_DELINEATE", { feature_count: 6 }, true)).toBe(
      "Parcel processing completed — 6 preliminary plot candidates generated. Survey/FMB verification required. Imagery and map layers refreshed.",
    );
  });

  it("shows only the active imagery's unverified AI candidates while preserving reviewed and non-AI buildings", () => {
    const building = (id: string, overrides: Partial<GeoFeature> = {}): GeoFeature => ({
      id,
      project_id: "project-1",
      geometry: { type: "Polygon", coordinates: [] },
      source: "AI_CANDIDATE",
      source_reference: "imagery:imagery-1",
      confidence: 0.9,
      model_version: "building-segmentation-c2-v1",
      status: "AI_PRELIMINARY",
      verification_status: "UNVERIFIED",
      processed_at: "2026-09-24T00:00:00Z",
      properties: {},
      ...overrides,
    });
    const visible = buildingsForActiveImagery([
      building("active"),
      building("stale", { source_reference: "imagery:imagery-2" }),
      building("reviewed", { source_reference: "imagery:imagery-2", status: "VERIFIED", verification_status: "VERIFIED" }),
      building("manual", { source: "MANUAL_DRAWN", source_reference: "manual:survey" }),
    ], "imagery-1");

    expect(visible.map((item) => item.id)).toEqual(["active", "reviewed", "manual"]);
  });

  it("filters unverified AI land-use candidates to the active imagery", () => {
    const landUse = (id: string, overrides: Partial<GeoFeature> = {}): GeoFeature => ({
      id,
      project_id: "project-1",
      geometry: { type: "MultiPolygon", coordinates: [] },
      source: "AI_CANDIDATE",
      source_reference: "imagery:imagery-1",
      confidence: 0.8,
      model_version: "segformer-b2-worldcover-2021-tamilnadu-v1",
      status: "AI_PRELIMINARY",
      verification_status: "UNVERIFIED",
      processed_at: "2026-09-28T00:00:00Z",
      properties: { land_use_class: "CROPLAND" },
      ...overrides,
    });
    const visible = landUseForActiveImagery([
      landUse("active"),
      landUse("stale", { source_reference: "imagery:imagery-2" }),
      landUse("reviewed", { source_reference: "imagery:imagery-2", status: "VERIFIED", verification_status: "VERIFIED" }),
    ], "imagery-1");

    expect(visible.map((item) => item.id)).toEqual(["active", "reviewed"]);
  });

  it("shows only active imagery AI plot candidates while preserving reviewed parcels", () => {
    const aiParcel = (id: string, reference: string, overrides: Partial<Parcel> = {}): Parcel => ({
      ...parcel,
      id,
      external_identifier: id,
      source: "AI_VISIBLE_BOUNDARY",
      source_reference: reference,
      ai_boundary_status: "AI_PRELIMINARY",
      verification_status: "UNVERIFIED",
      current_version: {
        ...parcel.current_version,
        id: `version-${id}`,
        source: "AI_VISIBLE_BOUNDARY",
        source_reference: reference,
      },
      ...overrides,
    });
    const visible = parcelsForActiveImagery([
      aiParcel("P-1001", "imagery:imagery-1:parcel-candidate"),
      aiParcel("P-1002", "imagery:imagery-2:parcel-candidate"),
      aiParcel("P-1003", "imagery:imagery-2:parcel-candidate", { verification_status: "VERIFIED" }),
      parcel,
    ], "imagery-1");

    expect(visible.map((item) => item.id)).toEqual(["P-1001", "P-1003", "parcel-1"]);
  });

  it("loads separate layer collections and renders selected parcel provenance and topology details", async () => {
    installProjectFetch();
    renderPage();
    await screen.findByRole("heading", { name: /project cadastral viewer/i });
    const map = screen.getByTestId("gis-map");
    expect(map).toHaveAttribute("data-parcel-count", "2");
    expect(map).toHaveAttribute("data-building-count", "1");
    expect(map).toHaveAttribute("data-road-count", "1");
    expect(map).toHaveAttribute("data-land-use-count", "1");
    expect(map).toHaveAttribute("data-topology-count", "1");

    fireEvent.click(screen.getByRole("button", { name: /select parcel/i }));
    expect(await screen.findByRole("heading", { name: "Draft parcel A" })).toBeInTheDocument();
    expect(screen.getByText("survey-2026")).toBeInTheDocument();
    expect(screen.getByText("120 m²")).toBeInTheDocument();
    expect(screen.getByText("1,291.67 sq ft")).toBeInTheDocument();
    expect(screen.getByText(/saving appends a new immutable version/i)).toBeInTheDocument();
    expect(await screen.findByText(/IMPORT.*system/i)).toBeInTheDocument();
    expect(screen.getByText(/NEIGHBOUR_OVERLAP/)).toBeInTheDocument();
    expect(screen.getByText(/overlaps a neighbour/i)).toBeInTheDocument();
    expect(screen.getByText(/4.25 m² affected/i)).toBeInTheDocument();
    expect(await screen.findByText(/EXACT SURVEY IDENTIFIER/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open source record/i })).toHaveAttribute("href", "/projects/project-1/documents?documentId=doc-1");

    fireEvent.click(screen.getByRole("button", { name: /select road/i }));
    expect(await screen.findByRole("heading", { name: "ROAD" })).toBeInTheDocument();
    expect(screen.getByText("155.5 m")).toBeInTheDocument();
  });

  it("opens the preserved WebGIS run with the actual saved output counts", async () => {
    installProjectFetch();
    renderPage();
    await screen.findByRole("heading", { name: /project cadastral viewer/i });
    expect(screen.getByRole("region", { name: /saved webgis demonstrations/i })).toBeInTheDocument();
    expect(screen.getByAltText(/Vegas building, road & plot demo source imagery preview/i)).toHaveAttribute("src", "/demo/webgis-vegas-preview.png");
    fireEvent.click(screen.getByRole("button", { name: /open.*vegas building, road & plot demo/i }));
    await waitFor(() => {
      expect(screen.getByTestId("gis-map")).toHaveAttribute("data-building-count", "29");
      expect(screen.getByTestId("gis-map")).toHaveAttribute("data-road-count", "9");
      expect(screen.getByTestId("gis-map")).toHaveAttribute("data-parcel-count", "42");
    });
    expect(screen.getByText(/preserved SIH walkthrough evidence/i)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /imagery and geoai controls/i })).not.toBeInTheDocument();
  });

  it("persists layer visibility per project", async () => {
    installProjectFetch();
    const first = renderPage();
    await screen.findByRole("heading", { name: /project cadastral viewer/i });
    fireEvent.click(screen.getByRole("checkbox", { name: "roads" }));
    expect(screen.getByRole("checkbox", { name: "roads" })).not.toBeChecked();
    await waitFor(() => expect(localStorage.getItem("gis-layer-visibility:project-1")).toContain('"roads":false'));
    first.unmount();
    renderPage();
    await screen.findByRole("heading", { name: /project cadastral viewer/i });
    expect(screen.getByRole("checkbox", { name: "roads" })).not.toBeChecked();
  });

  it("supports a manual GIS refresh", async () => {
    installProjectFetch();
    renderPage();
    await screen.findByRole("heading", { name: /project cadastral viewer/i });
    const fetchMock = vi.mocked(fetch);
    const before = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /refresh gis data/i }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before));
    expect(await screen.findByText(/last synced/i)).toBeInTheDocument();
  });

  it("falls back to the preserved WebGIS demo when the live API is unavailable", async () => {
    installProjectFetch();
    const demoFetch = vi.mocked(fetch);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/demo/webgis-demo-result.json")) return demoFetch(input, init);
      return new Response(JSON.stringify({ error: { message: "Forbidden" } }), { status: 403 });
    }));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("gis-map")).toHaveAttribute("data-building-count", "29");
      expect(screen.getByTestId("gis-map")).toHaveAttribute("data-road-count", "9");
      expect(screen.getByTestId("gis-map")).toHaveAttribute("data-parcel-count", "42");
    });
    expect(screen.getByText(/preserved SIH walkthrough evidence/i)).toBeInTheDocument();
    expect(screen.queryByText(/map unavailable/i)).not.toBeInTheDocument();
  });

  it("handles an empty project safely", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("/users/me") ? new Response(JSON.stringify(currentUser), { status: 200 }) : new Response(JSON.stringify(page([])), { status: 200 })));
    renderPage();
    await waitFor(() => expect(screen.getByText(/no gis features are available/i)).toBeInTheDocument());
    expect(screen.getByText(/building footprints remain separate/i)).toBeInTheDocument();
  });

  it("renders legacy metadata-only imagery without requesting a preview", async () => {
    const legacy = { id: "legacy-imagery", project_id: "project-1", file_id: null, filename: null, source_reference: "H2-SYNTHETIC-TN-DEMO:ORTHOMOSAIC", source_crs: "EPSG:4326", coordinate_space: "WORLD", metadata: { demo: true, registration_status: "READY" }, registration_job_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
    installProjectFetch([legacy]);
    renderPage();
    expect(await screen.findByRole("heading", { name: /project cadastral viewer/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /metadata-only imagery/i })).toBeInTheDocument();
    expect(screen.getByText(/has no private source file/i)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/imagery/legacy-imagery/preview-url"))).toBe(false);
  });

  it("keeps imagery controls above the map stage and switches the basemap selection", async () => {
    installProjectFetch();
    renderPage();
    await screen.findByRole("heading", { name: /project cadastral viewer/i });
    expect(screen.getByRole("region", { name: /imagery and geoai controls/i })).toBeVisible();
    expect(screen.getByLabelText(/upload geotiff/i)).toBeEnabled();
    const street = screen.getByRole("button", { name: "Street" });
    const satellite = screen.getByRole("button", { name: "Satellite" });
    expect(street).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(satellite);
    expect(satellite).toHaveAttribute("aria-pressed", "true");
    expect(street).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("gis-map").parentElement).toHaveClass("map-stage");
  });

  it("lets an authorized user queue land-use GeoAI for ready private imagery", async () => {
    const imagery = [{ id: "imagery-1", project_id: "project-1", file_id: "file-1", filename: "sentinel-rgbnir.tif", source_reference: null, source_crs: "EPSG:4326", coordinate_space: "WORLD", metadata: { registration_status: "READY", width: 12000, height: 12000, band_count: 4 }, registration_job_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }];
    installProjectFetch(imagery);
    renderPage();
    await screen.findByRole("button", { name: "Run Land-use GeoAI" });
    fireEvent.click(screen.getByRole("button", { name: "Run Land-use GeoAI" }));
    expect(await screen.findByText(/Land-use processing queued/i)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/geoai/jobs"))).toBe(true);
  });

  it("shows LULC color names and TIFF proportions for the active imagery", async () => {
    const imagery = [{
      id: "imagery-1", project_id: "project-1", file_id: "file-1", filename: "sentinel-rgbnir.tif",
      source_reference: null, source_crs: "EPSG:4326", coordinate_space: "WORLD",
      metadata: {
        registration_status: "READY", width: 12000, height: 12000, band_count: 4,
        lulc_distribution: [
          { code: 20, class_name: "SHRUBLAND", pixel_count: 4945, proportion: 0.4945 },
          { code: 40, class_name: "CROPLAND", pixel_count: 2929, proportion: 0.2929 },
          { code: 10, class_name: "TREE_COVER", pixel_count: 982, proportion: 0.0982 },
        ],
      },
      registration_job_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    }];
    installProjectFetch(imagery);
    renderPage();
    expect(await screen.findByRole("region", { name: "Land-use composition" })).toBeInTheDocument();
    expect(screen.getByText("Shrubland")).toBeInTheDocument();
    expect(screen.getByText("49.45%")).toBeInTheDocument();
    expect(screen.getByText("Cropland")).toBeInTheDocument();
    expect(screen.getByText("29.29%")).toBeInTheDocument();
    expect(screen.getByLabelText("Shrubland color")).toHaveStyle({ backgroundColor: "#ffbb22" });

    fireEvent.click(screen.getByRole("checkbox", { name: "land use" }));
    expect(screen.queryByRole("region", { name: "Land-use composition" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "land use" }));
    expect(screen.getByRole("region", { name: "Land-use composition" })).toBeInTheDocument();
  });

  it("lets an authorized user queue preliminary parcel delineation", async () => {
    const imagery = [{ id: "imagery-1", project_id: "project-1", file_id: "file-1", filename: "orthomosaic.tif", source_reference: null, source_crs: "EPSG:4326", coordinate_space: "WORLD", metadata: { registration_status: "READY", width: 1000, height: 1000 }, registration_job_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }];
    installProjectFetch(imagery);
    renderPage();
    await screen.findByRole("button", { name: "Generate Plot Candidates" });
    fireEvent.click(screen.getByRole("button", { name: "Generate Plot Candidates" }));
    expect(await screen.findByText(/Parcel processing queued/i)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/geoai/jobs"))).toBe(true);
  });

  it("lets an authorized user queue Road GeoAI for ready private imagery", async () => {
    const imagery = [{ id: "imagery-1", project_id: "project-1", file_id: "file-1", filename: "orthomosaic.tif", source_reference: null, source_crs: "EPSG:4326", coordinate_space: "WORLD", metadata: { registration_status: "READY", width: 10, height: 10 }, registration_job_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }];
    installProjectFetch(imagery);
    renderPage();
    await screen.findByRole("button", { name: "Run Road GeoAI" });
    fireEvent.click(screen.getByRole("button", { name: "Run Road GeoAI" }));
    expect(await screen.findByText(/Road processing queued/i)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/geoai/jobs"))).toBe(true);
  });
});
