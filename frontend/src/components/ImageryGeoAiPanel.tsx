import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ApiError,
  createGeoAIJob,
  loadGeoAIJob,
  loadImageryPreview,
  uploadAndRegisterImagery,
  type GeoAIJob,
  type ImageryAsset,
  type ImageryPreview,
} from "../api/gis";

export function ImageryGeoAiPanel({
  projectId,
  assets,
  canUpload,
  canProcess,
  onChanged,
  onPreview,
  onZoomToImagery,
}: {
  projectId: string;
  assets: ImageryAsset[];
  canUpload: boolean;
  canProcess: boolean;
  onChanged: () => Promise<void>;
  onPreview: (preview: ImageryPreview | null) => void;
  onZoomToImagery: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const selected =
    assets.find((asset) => asset.id === selectedId) ??
    assets.find((asset) => asset.metadata.registration_status === "READY") ??
    null;

  useEffect(() => {
    if (selectedId && assets.some((asset) => asset.id === selectedId)) return;
    const fallback =
      assets.find(
        (asset) =>
          Boolean(asset.file_id) &&
          asset.metadata.registration_status === "READY",
      ) ??
      assets.find((asset) => Boolean(asset.file_id)) ??
      null;
    if (fallback?.id !== selectedId) setSelectedId(fallback?.id ?? null);
  }, [assets, selectedId]);

  const upload = useMutation({
    mutationFn: (file: File) => uploadAndRegisterImagery(projectId, file),
    onSuccess: async (createdAsset) => {
      setSelectedId(createdAsset.id);
      setMessage("GeoTIFF uploaded privately and queued for registration.");
      await onChanged();
    },
    onError: (error) =>
      setMessage(
        error instanceof Error ? error.message : "The imagery upload failed.",
      ),
  });

  const hasPrivateFile = Boolean(selected?.file_id);

  const preview = useQuery({
    queryKey: ["imagery-preview", projectId, selected?.id],
    queryFn: () => loadImageryPreview(projectId, selected!.id),
    enabled: Boolean(
      selected &&
        hasPrivateFile &&
        selected.metadata.registration_status === "READY",
    ),
    staleTime: 8 * 60_000,
  });

  const job = useQuery<GeoAIJob>({
    queryKey: ["geoai-job", projectId, activeJobId],
    queryFn: () => loadGeoAIJob(projectId, activeJobId!),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "COMPLETED" || status === "FAILED" ? false : 1500;
    },
  });

  const run = useMutation({
    mutationFn: () =>
      createGeoAIJob(projectId, {
        job_type: "BUILDING_VECTORIZE",
        source_type: "REGISTERED_IMAGERY",
        source_payload: {},
        imagery_asset_id: selected!.id,
        source_reference: `imagery:${selected!.id}`,
        idempotency_key: `buildings:${selected!.id}:${crypto.randomUUID()}`,
      }),
    onSuccess: (createdJob) => {
      setActiveJobId(createdJob.id);
      setMessage(
        `Building processing ${createdJob.status.toLowerCase()}. Waiting for completion...`,
      );
    },
    onError: (error) =>
      setMessage(
        error instanceof ApiError
          ? error.message
          : "Building processing could not be queued.",
      ),
  });

  useEffect(() => {
    if (preview.data) {
      onPreview(preview.data);
      return;
    }
    if (
      !selected ||
      !hasPrivateFile ||
      selected.metadata.registration_status !== "READY"
    ) {
      onPreview(null);
    }
  }, [
    hasPrivateFile,
    onPreview,
    preview.data,
    selected,
  ]);

  useEffect(() => {
    if (!job.data) return;

    if (job.data.status === "QUEUED") {
      setMessage("Building processing queued...");
      return;
    }

    if (job.data.status === "PROCESSING") {
      setMessage("Building processing in progress...");
      return;
    }

    if (job.data.status === "COMPLETED") {
      setMessage("Building processing completed. Refreshing imagery and map layers...");
      setActiveJobId(null);
      void (async () => {
        await onChanged();
        const refreshedPreview = await preview.refetch();
        if (refreshedPreview.data) onPreview(refreshedPreview.data);
        setMessage("Building processing completed. Imagery and map layers refreshed.");
        onZoomToImagery();
      })();
      return;
    }

    if (job.data.status === "FAILED") {
      setMessage("Building processing failed.");
      setActiveJobId(null);
    }
  }, [job.data, onChanged, onPreview, onZoomToImagery, preview]);

  return (
    <section className="imagery-panel" aria-label="Imagery and GeoAI controls">
      <p className="eyebrow">Imagery and building GeoAI</p>
      <h2>Registered GeoTIFFs</h2>

      {canUpload && (
        <label className="imagery-upload">
          Upload GeoTIFF
          <input
            type="file"
            accept=".tif,.tiff,image/tiff,application/geotiff"
            disabled={upload.isPending}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) upload.mutate(file);
              event.currentTarget.value = "";
            }}
          />
        </label>
      )}

      {!canUpload && (
        <p className="panel-note">
          `imagery:upload` is required to add private project imagery.
        </p>
      )}

      <label className="imagery-select">
        Active imagery
        <select
          value={selected?.id ?? ""}
          onChange={(event) => setSelectedId(event.target.value || null)}
        >
          <option value="">No imagery selected</option>
          {assets.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.filename ?? "Metadata-only imagery"} ·{" "}
              {String(
                asset.metadata.registration_status ?? "METADATA_ONLY",
              )}
            </option>
          ))}
        </select>
      </label>

      {selected && (
        <dl className="imagery-metadata">
          <dt>CRS</dt>
          <dd>{selected.source_crs ?? "Registering"}</dd>

          <dt>Size</dt>
          <dd>
            {String(selected.metadata.width ?? "?")} ×{" "}
            {String(selected.metadata.height ?? "?")}
          </dd>

          <dt>Pixel size</dt>
          <dd>
            {String(selected.metadata.resolution_x ?? "?")} ×{" "}
            {String(selected.metadata.resolution_y ?? "?")}
          </dd>

          <dt>Coordinate space</dt>
          <dd>{selected.coordinate_space}</dd>
        </dl>
      )}

      {selected && !hasPrivateFile && (
        <p className="panel-note">
          Metadata-only legacy imagery has no private source file, so preview
          and building processing are unavailable.
        </p>
      )}

      {selected?.metadata.registration_status === "READY" &&
        hasPrivateFile &&
        canProcess && (
          <button
            type="button"
            className="primary-action"
            disabled={run.isPending || Boolean(activeJobId)}
            onClick={() => run.mutate()}
          >
            {activeJobId ? "Building GeoAI running..." : "Run Building GeoAI"}
          </button>
        )}

      {selected?.metadata.registration_status === "READY" &&
        hasPrivateFile &&
        !canProcess && (
          <p className="panel-note">
            `geoai:process` is required to run building extraction.
          </p>
        )}

      {preview.isLoading && <p>Loading signed map preview...</p>}

      {message && (
        <p
          className={message.includes("failed") ? "error-copy" : "success-copy"}
          role="status"
        >
          {message}
        </p>
      )}

      <p className="panel-note">
        Original imagery remains private. The map uses a time-limited derived
        preview. Building footprints are AI preliminary and never parcel
        boundaries.
      </p>
    </section>
  );
}
