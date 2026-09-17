import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ComponentProps } from "react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Parcel, ParcelVersionSaveResult } from "../api/gis";
import { ParcelEditor } from "../components/ParcelEditor";
import { basicPolygonProblems, geodesicAreaM2, insertVertex, removeVertex, type PolygonGeometry } from "../features/gis/geometry";
import { useParcelEditor } from "../features/gis/useParcelEditor";

const parcel = { id: "parcel-1", project_id: "project-1", external_identifier: "Draft", source: "EXISTING_GIS", source_reference: null, status: "DRAFT", verification_status: "UNVERIFIED", current_geometry_version: 1, coordinate_space: "WORLD", source_crs: "EPSG:4326", confidence: null, model_version: null, ai_boundary_status: null, requires_survey: false, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", current_version: { id: "version-1", version: 1, geometry: { type: "Polygon", coordinates: [[[77, 28], [77.001, 28], [77.001, 28.001], [77, 28.001], [77, 28]]] }, source: "EXISTING_GIS", source_reference: null, coordinate_space: "WORLD", source_crs: "EPSG:4326", area_m2: 10_000, area_sqft: 107_639, change_reason: null, validation_status: "VALID", created_by_user_id: null, created_by_type: "IMPORT" as const, processed_at: null, created_at: "2026-01-01T00:00:00Z" } } satisfies Parcel;

function EditorHarness({ canEdit = true, value = parcel, onSaved = vi.fn() }: { canEdit?: boolean; value?: Parcel; onSaved?: (result: ParcelVersionSaveResult) => void }) {
  const editor = useParcelEditor();
  return <ParcelEditor projectId="project-1" parcel={value} editor={editor} canEdit={canEdit} onSaved={onSaved} />;
}

function renderEditor(props?: ComponentProps<typeof EditorHarness>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><EditorHarness {...props} /></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("ParcelEditor", () => {
  it("keeps an isolated edit history for move, add, delete, undo, redo, and reset", () => {
    const original = structuredClone(parcel.current_version.geometry); const { result } = renderHook(() => useParcelEditor());
    act(() => expect(result.current.enter(parcel)).toBeNull());
    act(() => result.current.moveVertex(0, [77.0002, 28])); act(() => result.current.commitDraggedVertex());
    expect(result.current.session?.working.coordinates[0][0]).toEqual([77.0002, 28]); expect(parcel.current_version.geometry).toEqual(original);
    act(() => result.current.addVertexAt([77.0006, 28])); expect(result.current.session?.working.coordinates[0]).toHaveLength(6);
    act(() => result.current.selectVertex(1)); act(() => expect(result.current.deleteSelectedVertex()).toBeNull()); expect(result.current.session?.working.coordinates[0]).toHaveLength(5);
    act(() => result.current.undo()); expect(result.current.session?.working.coordinates[0]).toHaveLength(6);
    act(() => result.current.redo()); expect(result.current.session?.working.coordinates[0]).toHaveLength(5);
    act(() => result.current.toggleOriginal()); expect(result.current.session?.showOriginal).toBe(false);
    act(() => result.current.reset()); expect(result.current.session?.working).toEqual(original);
  });

  it("protects the minimum ring and provides a geodesic preview with basic coordinate validation", () => {
    const triangle = { type: "Polygon", coordinates: [[[77, 28], [77.001, 28], [77, 28.001], [77, 28]]] } as PolygonGeometry;
    const polygon = parcel.current_version.geometry as PolygonGeometry;
    expect(removeVertex(triangle, 0)).toBeNull(); const area = geodesicAreaM2(polygon);
    expect(area).not.toBeNull(); expect(geodesicAreaM2(insertVertex(polygon, 0, [77.0005, 28]))).toBeCloseTo(area!, 2);
    expect(basicPolygonProblems({ type: "Polygon", coordinates: [[[200, 28], [77, 28], [77, 28.001], [200, 28]]] } as PolygonGeometry)).not.toHaveLength(0);
  });

  it("submits a WGS84 immutable-version request with a reason and expected version", async () => {
    const onSaved = vi.fn(); const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(JSON.stringify({ version: { ...parcel.current_version, id: "version-2", version: 2 }, status: "VALID", area_before_m2: 10_000, area_after_m2: 10_010, issues: [] }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock); renderEditor({ onSaved });
    fireEvent.click(screen.getByRole("button", { name: "Edit Boundary" }));
    fireEvent.change(screen.getByRole("textbox", { name: /change reason/i }), { target: { value: "Adjusted eastern boundary" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ expected_current_version: 1, source_crs: "EPSG:4326", coordinate_space: "WORLD", change_reason: "Adjusted eastern boundary" });
  });

  it("keeps edit mode open on a version conflict and hides edit controls without permission", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "PARCEL_VERSION_CONFLICT", message: "Changed" } }), { status: 409 })));
    renderEditor(); fireEvent.click(screen.getByRole("button", { name: "Edit Boundary" }));
    fireEvent.change(screen.getByRole("textbox", { name: /change reason/i }), { target: { value: "Adjusted boundary" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    expect(await screen.findByText(/changed while you were editing/i)).toBeInTheDocument();
    expect(screen.getByText(/edit mode/i)).toBeInTheDocument();

    renderEditor({ canEdit: false });
    expect(screen.queryByRole("button", { name: "Edit Boundary" })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only access/i)).toBeInTheDocument();
  });

  it("does not enter edit mode for missing or MultiPolygon geometry", () => {
    renderEditor({ value: { ...parcel, current_version: { ...parcel.current_version, geometry: null } } });
    expect(screen.getByText(/cannot enter standard edit mode/i)).toBeInTheDocument();
    renderEditor({ value: { ...parcel, current_version: { ...parcel.current_version, geometry: { type: "MultiPolygon", coordinates: [] } } } });
    expect(screen.getByText(/multipolygon editing is disabled/i)).toBeInTheDocument();
  });
});
