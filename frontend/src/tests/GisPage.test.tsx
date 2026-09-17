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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={["/projects/project-1/gis"]}><Routes><Route path="/projects/:projectId/gis" element={<GisPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("GisPage", () => {
  it("loads separate layer collections and renders selected parcel provenance", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/users/me")) return new Response(JSON.stringify({ id: "user-1", login_id: "SUR-TN-1", email: "surveyor@example.invalid", full_name: "Surveyor", roles: ["SURVEYOR"], permissions: ["geo:read", "geo:edit_draft"], project_memberships: [{ project_id: "project-1", role: "SURVEYOR" }] }), { status: 200 });
      if (url.includes("/versions")) return new Response(JSON.stringify(page([parcel.current_version])), { status: 200 });
      if (url.includes("/parcels?")) return new Response(JSON.stringify(page([parcel, notDeterminedParcel])), { status: 200 });
      if (url.includes("/buildings")) return new Response(JSON.stringify(page([{ id: "building-1" }])), { status: 200 });
      if (url.includes("/roads")) return new Response(JSON.stringify(page([{ id: "road-1", source: "EXISTING_GIS", source_reference: "road-survey", confidence: 0.9, model_version: null, status: "DRAFT", verification_status: "UNVERIFIED", processed_at: null, geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] }, properties: { road_class: "ROAD", length_m: 155.5 } }])), { status: 200 });
      if (url.includes("/land-use")) return new Response(JSON.stringify(page([{ id: "land-use-1" }])), { status: 200 });
      return new Response(JSON.stringify(page([{ id: "topology-1", parcel_id: "parcel-1", related_parcel_id: null }])), { status: 200 });
    }));
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

    fireEvent.click(screen.getByRole("checkbox", { name: "roads" }));
    expect(screen.getByRole("checkbox", { name: "roads" })).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: /select road/i }));
    expect(await screen.findByRole("heading", { name: "ROAD" })).toBeInTheDocument();
    expect(screen.getByText("155.5 m")).toBeInTheDocument();
  });

  it("shows a clear unauthorized state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { message: "Forbidden" } }), { status: 403 })));
    renderPage();
    expect(await screen.findByText(/do not have permission/i)).toBeInTheDocument();
  });

  it("handles an empty project safely", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(page([])), { status: 200 })));
    renderPage();
    await waitFor(() => expect(screen.getByText(/no gis features are available/i)).toBeInTheDocument());
    expect(screen.getByText(/building footprints remain separate/i)).toBeInTheDocument();
  });
});
