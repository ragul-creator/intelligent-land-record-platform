import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, uploadAndRegisterImagery } from "../api/gis";

afterEach(() => vi.restoreAllMocks());

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
