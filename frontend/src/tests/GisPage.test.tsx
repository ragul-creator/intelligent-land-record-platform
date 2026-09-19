import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GisPage } from "../pages/GisPage";

vi.mock("../components/GisMap", () => ({
  GisMap: ({ parcels, buildings, roads, landUse, topologyParcelIds, onParcelSelect, onFeatureSelect }: { parcels: Array<{ id: string }>; buildings: unknown[]; roads: Array<{ id: string }>; landUse: unknown[]; topologyParcelIds: string[]; onParcelSelect: (id: string) => void; onFeatureSelect: (kind: "ROAD", id: string) => void }) => (
    <div data-testid="gis-map" data-parcel-count={parcels.length} data-building-count={buildings.length} data-road-count={roads.length} data-land-use-count={landUse.length} data-topology-count={topologyParcelIds.length}>
      {parcels[0] && <button type="button" onClick={() => onParcelSelect(parcels[0].id)}>Select parcel</button>}
      {roads[0] && <button type="button" onClick={() => onFeatureSelect("ROAD", roads[0].id)}>Select road</button>}
    </div>
  ),
}));

const page = (items: unknown[]) => ({ items, page: { limit: 500, offset: 0, total: items.length } });
const parcel = {
  id: "parcel-1", project_id: "project-1", external_identifier: "Draft parcel A", source: "EXISTING_GIS", source_reference: "survey-2026", status: "DRAFT", verification_status: "UNVERIFIED", current_geometry_version: 1, coordinate_space: "WORLD", source_crs: "EPSG:32643", confidence: 0.82, model_version: null, ai_boundary_status: "NOT_DETERMINED", requires_survey: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  current_version: { id: "version-1", version: 1, geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] }, source: "EXISTING_GIS", source_reference: "survey-2026", coordinate_space: "WORLD", source_crs: "EPSG:32643", area_m2: 120, area_sqft: 1291.67, change_reason: null, validation_status: "VALID", created_by_user_id: null, created_by_type: "IMPORT", processed_at: "2026-01-01T00:00:00Z", created_at: "2026-01-01T00:00:00Z" },
};
const notDeterminedParcel = { ...parcel, id: "parcel-no-geometry", external_identifier: "Not determined", ai_boundary_status: "NOT_DETERMINED", current_version: { ...parcel.current_version, id: "version-no-geometry", geometry: null } };
const currentUser = { id: "user-1", login_id: "SUR-TN-1", email: "surveyor@example.invalid", full_name: "Surveyor", roles: ["SURVEYOR"], permissions: ["geo:read", "geo:edit_draft", "imagery:upload", "geoai:process"], project_memberships: [{ project_id: "project-1", role: "SURVEYOR" }] };
const topologyIssue = { id: "topology-1", project_id: "project-1", parcel_id: "parcel-1", related_parcel_id: null, code: "NEIGHBOUR_OVERLAP", severity: "REVIEW", area_m2: 4.25, message: "Edited parcel overlaps a neighbour.", resolved: false, created_at: "2026-01-01T00:00:00Z" };

function installProjectFetch(imageryAssets: unknown[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/users/me")) return new Response(JSON.stringify(currentUser), { status: 200 });
    if (url.includes("/versions")) return new Response(JSON.stringify(page([parcel.current_version])), { status: 200 });
    if (url.includes("/record-parcel-links")) return new Response(JSON.stringify(page([{ id: "link-1", project_id: "project-1", document_id: "doc-1", document_validation_result_id: "validation-1", parcel_id: "parcel-1", parcel_display_identifier: "Draft parcel A", link_status: "CONFIRMED", link_method: "EXACT_SURVEY_IDENTIFIER", confidence: 0.97, rationale: {}, provenance: {}, review_required: false, review_task_id: null, review_reason: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }])), { status: 200 });
    if (url.includes("/parcels?")) return new Response(JSON.stringify(page([parcel, notDeterminedParcel])), { status: 200 });
    if (url.includes("/buildings")) return new Response(JSON.stringify(page([{ id: "building-1" }])), { status: 200 });
    if (url.includes("/roads")) return new Response(JSON.stringify(page([{ id: "road-1", source: "EXISTING_GIS", source_reference: "road-survey", confidence: 0.9, model_version: null, status: "DRAFT", verification_status: "UNVERIFIED", processed_at: null, geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] }, properties: { road_class: "ROAD", length_m: 155.5 } }])), { status: 200 });
    if (url.includes("/land-use")) return new Response(JSON.stringify(page([{ id: "land-use-1" }])), { status: 200 });
    if (url.includes("/topology-errors")) return new Response(JSON.stringify(page([topologyIssue])), { status: 200 });
    if (url.includes("/imagery")) return new Response(JSON.stringify(page(imageryAssets)), { status: 200 });
    return new Response(JSON.stringify(page([])), { status: 200 });
  }));
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={["/projects/project-1/gis"]}><Routes><Route path="/projects/:projectId/gis" element={<GisPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

describe("GisPage", () => {
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

  it("shows a clear unauthorized state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { message: "Forbidden" } }), { status: 403 })));
    renderPage();
    expect(await screen.findByText(/do not have permission/i)).toBeInTheDocument();
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
});
