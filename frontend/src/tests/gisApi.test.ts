import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, loadGisProject, loadParcelVersions, uploadAndRegisterImagery } from "../api/gis";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("GIS pagination", () => {
  it("loads later pages of every project layer", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const parsed = new URL(url);
      const offset = Number(parsed.searchParams.get("offset"));
      const layer = parsed.pathname.split("/").pop();
      return new Response(JSON.stringify({ items: [{ id: `${layer}-${offset}` }], page: { limit: 1, offset, total: 2 } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await loadGisProject("project-1");
    for (const items of Object.values(result)) expect(items).toHaveLength(2);
    expect(result.parcels.map(item => item.id)).toEqual(["parcels-0", "parcels-1"]);
    expect(fetchMock).toHaveBeenCalledTimes(12);
  });

  it("loads complete parcel history", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const offset = Number(new URL(url).searchParams.get("offset"));
      return new Response(JSON.stringify({ items: [{ version: offset + 1 }], page: { limit: 1, offset, total: 3 } }));
    }));
    expect((await loadParcelVersions("project-1", "parcel-1")).map(item => item.version)).toEqual([1, 2, 3]);
  });

  it("fails visibly when a page ends before the reported total", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], page: { limit: 100, offset: 0, total: 2 } }))));
    await expect(loadParcelVersions("project-1", "parcel-1")).rejects.toMatchObject({ code: "INCOMPLETE_PAGE" });
  });
});

describe("uploadAndRegisterImagery", () => {
  it("reports a browser-reachable storage failure and does not register imagery", async () => {
    vi.stubGlobal("crypto", { subtle: { digest: vi.fn(async () => new Uint8Array(32).buffer) } });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ file_id: "file-1", upload_url: "http://localhost:9001/land-records/object?signature=valid", required_headers: { "Content-Type": "image/tiff" } }), { status: 201 }))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const file = {
      name: "orthomosaic.tif",
      type: "image/tiff",
      size: 4,
      arrayBuffer: vi.fn(async () => new Uint8Array([1, 2, 3, 4]).buffer),
    } as unknown as File;

    await expect(uploadAndRegisterImagery("project-1", file)).rejects.toMatchObject({
      code: "IMAGERY_UPLOAD_FAILED",
      message: "The browser could not reach private object storage. Check the MinIO upload endpoint and CORS configuration.",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[1][0])).toContain("localhost:9001");
  });
});
