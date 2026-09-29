import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
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

type ImageryGeoAiJobKind = "BUILDING_VECTORIZE" | "ROAD_VECTORIZE" | "LAND_USE_VECTORIZE" | "PARCEL_DELINEATE";

const geoAiLabel = (kind: ImageryGeoAiJobKind | null) =>
  kind === "ROAD_VECTORIZE" ? "Road" : kind === "LAND_USE_VECTORIZE" ? "Land-use" : kind === "PARCEL_DELINEATE" ? "Parcel" : "Building";

const LULC_STYLE: Record<string, { label: string; color: string }> = {
  TREE_COVER: { label: "Tree cover", color: "#006400" },
  SHRUBLAND: { label: "Shrubland", color: "#ffbb22" },
  GRASSLAND: { label: "Grassland", color: "#ffff4c" },
  CROPLAND: { label: "Cropland", color: "#f096ff" },
  BUILT_UP: { label: "Built-up", color: "#fa0000" },
  BARE_SPARSE_VEGETATION: { label: "Bare / sparse vegetation", color: "#b4b4b4" },
  SNOW_ICE: { label: "Snow / ice", color: "#f0f0f0" },
  PERMANENT_WATER_BODIES: { label: "Permanent water", color: "#0064c8" },
  HERBACEOUS_WETLAND: { label: "Herbaceous wetland", color: "#0096a0" },
  MANGROVES: { label: "Mangroves", color: "#00cf75" },
  MOSS_LICHEN: { label: "Moss / lichen", color: "#fae6a0" },
};

type LulcDistributionRow = { class_name: string; pixel_count: number; proportion: number; code?: number };

export function geoAiCompletionMessage(
  kind: ImageryGeoAiJobKind | null,
  outputReferences: Record<string, unknown>,
  refreshed: boolean,
): string {
  const label = geoAiLabel(kind);
  const rawCount = outputReferences.feature_count;
  const count = typeof rawCount === "number" && Number.isFinite(rawCount) && rawCount >= 0
    ? Math.trunc(rawCount)
    : null;
  const suffix = refreshed ? "Imagery and map layers refreshed." : "Refreshing imagery and map layers...";

  if (count === null) return `${label} processing completed. ${suffix}`;
  if (kind === "ROAD_VECTORIZE") {
    const result = count === 0 ? "No road candidates detected." : `${count} road candidate${count === 1 ? "" : "s"} detected.`;
    return `Road processing completed — ${result} ${suffix}`;
  }
  if (kind === "LAND_USE_VECTORIZE") {
    const result = count === 0 ? "No land-use regions detected." : `${count} land-use class region${count === 1 ? "" : "s"} detected.`;
    return `Land-use processing completed — ${result} ${suffix}`;
  }
  if (kind === "PARCEL_DELINEATE") {
    const result = count === 0 ? "No plot candidates could be generated." : `${count} preliminary plot candidate${count === 1 ? "" : "s"} generated.`;
    return `Parcel processing completed — ${result} Survey/FMB verification required. ${suffix}`;
  }
  const result = count === 0 ? "No building footprints detected." : `${count} building footprint${count === 1 ? "" : "s"} detected.`;
  return `Building processing completed — ${result} ${suffix}`;
}

export function ImageryGeoAiPanel({
  projectId,
  assets,
  canUpload,
  canProcess,
  showLandUseComposition,
  onChanged,
  onPreview,
  onZoomToImagery,
  fullscreenHost,
}: {
  projectId: string;
  assets: ImageryAsset[];
  canUpload: boolean;
  canProcess: boolean;
  showLandUseComposition: boolean;
  onChanged: () => Promise<void>;
  onPreview: (preview: ImageryPreview | null) => void;
  onZoomToImagery: () => void;
  fullscreenHost?: HTMLElement | null;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeJobKind, setActiveJobKind] = useState<ImageryGeoAiJobKind | null>(null);

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
  const landUseHasFourBands = Number(selected?.metadata.band_count ?? 0) >= 4;
  const lulcDistribution = Array.isArray(selected?.metadata.lulc_distribution)
    ? (selected?.metadata.lulc_distribution as LulcDistributionRow[])
        .filter((row) => row && typeof row.class_name === "string" && Number.isFinite(Number(row.proportion)))
        .sort((a, b) => Number(b.proportion) - Number(a.proportion))
    : [];

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
    mutationFn: (jobType: ImageryGeoAiJobKind) =>
      createGeoAIJob(projectId, {
        job_type: jobType,
        source_type: "REGISTERED_IMAGERY",
        source_payload: {},
        imagery_asset_id: selected!.id,
        source_reference: `imagery:${selected!.id}`,
        idempotency_key: `${jobType === "ROAD_VECTORIZE" ? "roads" : jobType === "LAND_USE_VECTORIZE" ? "land-use" : jobType === "PARCEL_DELINEATE" ? "parcels" : "buildings"}:${selected!.id}:${crypto.randomUUID()}`,
      }),
    onSuccess: (createdJob, jobType) => {
      setActiveJobId(createdJob.id);
      setActiveJobKind(jobType);
      const label = geoAiLabel(jobType);
      setMessage(
        `${label} processing ${createdJob.status.toLowerCase()}. Waiting for completion...`,
      );
    },
    onError: (error) =>
      setMessage(
        error instanceof ApiError
          ? error.message
          : "GeoAI processing could not be queued.",
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

    const label = geoAiLabel(activeJobKind);

    if (job.data.status === "QUEUED") {
      setMessage(`${label} processing queued...`);
      return;
    }

    if (job.data.status === "PROCESSING") {
      setMessage(`${label} processing in progress...`);
      return;
    }

    if (job.data.status === "COMPLETED") {
      const completedKind = activeJobKind;
      const outputReferences = job.data.output_references ?? {};
      setMessage(geoAiCompletionMessage(completedKind, outputReferences, false));
      setActiveJobId(null);
      setActiveJobKind(null);
      void (async () => {
        await onChanged();
        const refreshedPreview = await preview.refetch();
        if (refreshedPreview.data) onPreview(refreshedPreview.data);
        setMessage(geoAiCompletionMessage(completedKind, outputReferences, true));
        onZoomToImagery();
      })();
      return;
    }

    if (job.data.status === "FAILED") {
      setMessage(`${label} processing failed.`);
      setActiveJobId(null);
      setActiveJobKind(null);
    }
  }, [activeJobKind, job.data, onChanged, onPreview, onZoomToImagery, preview.refetch]);

  const fullscreenComposition = fullscreenHost && showLandUseComposition && selected && lulcDistribution.length > 0 ? createPortal(
    <aside className="fullscreen-lulc-composition" aria-label="Fullscreen land-use composition">
      <div className="fullscreen-lulc-heading">
        <strong>Land-use proportion</strong>
        <span>Classified pixels</span>
      </div>
      <div className="fullscreen-lulc-list">
        {lulcDistribution.map((row) => {
          const style = LULC_STYLE[row.class_name] ?? { label: row.class_name.replaceAll("_", " "), color: "#777777" };
          return (
            <div className="fullscreen-lulc-row" key={row.class_name}>
              <span className="lulc-swatch" style={{ backgroundColor: style.color }} aria-hidden="true" />
              <span>{style.label}</span>
              <strong>{(Number(row.proportion) * 100).toFixed(2)}%</strong>
            </div>
          );
        })}
      </div>
    </aside>,
    fullscreenHost,
  ) : null;

  const fullscreenActions = selected?.metadata.registration_status === "READY" && hasPrivateFile && canProcess ? (
    <div className="fullscreen-model-actions" aria-label="Fullscreen GeoAI model controls">
      <span className="fullscreen-control-title">GeoAI models</span>
      <button type="button" disabled={run.isPending || Boolean(activeJobId)} onClick={() => run.mutate("BUILDING_VECTORIZE")}>
        {activeJobKind === "BUILDING_VECTORIZE" && activeJobId ? "Building running..." : "Building"}
      </button>
      <button type="button" disabled={run.isPending || Boolean(activeJobId)} onClick={() => run.mutate("ROAD_VECTORIZE")}>
        {activeJobKind === "ROAD_VECTORIZE" && activeJobId ? "Road running..." : "Road"}
      </button>
      <button type="button" disabled={run.isPending || Boolean(activeJobId) || !landUseHasFourBands} onClick={() => run.mutate("LAND_USE_VECTORIZE")}>
        {activeJobKind === "LAND_USE_VECTORIZE" && activeJobId ? "Land use running..." : "Land use"}
      </button>
      <button type="button" disabled={run.isPending || Boolean(activeJobId)} onClick={() => run.mutate("PARCEL_DELINEATE")}>
        {activeJobKind === "PARCEL_DELINEATE" && activeJobId ? "Parcels running..." : "Parcels"}
      </button>
      {message && <span className={message.includes("failed") ? "fullscreen-job-status error-copy" : "fullscreen-job-status"} role="status">{message}</span>}
    </div>
  ) : null;

  return (
    <>
      {fullscreenHost && fullscreenActions ? createPortal(fullscreenActions, fullscreenHost) : null}
      {fullscreenComposition}
      <section className="imagery-panel" aria-label="Imagery and GeoAI controls">
      <p className="eyebrow">Imagery, buildings, roads, land-use and parcel intelligence</p>
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

      {showLandUseComposition && selected && lulcDistribution.length > 0 && (
        <section className="lulc-composition" aria-label="Land-use composition">
          <div className="lulc-composition-heading">
            <strong>Land-use composition</strong>
            <span>Proportion of valid classified pixels</span>
          </div>
          <div className="lulc-composition-table" role="table" aria-label="LULC color and proportion table">
            <div className="lulc-composition-row lulc-composition-header" role="row">
              <span role="columnheader">Color</span><span role="columnheader">Class</span><span role="columnheader">Proportion</span>
            </div>
            {lulcDistribution.map((row) => {
              const style = LULC_STYLE[row.class_name] ?? { label: row.class_name.replaceAll("_", " "), color: "#777777" };
              return (
                <div className="lulc-composition-row" role="row" key={row.class_name}>
                  <span role="cell"><span className="lulc-swatch" style={{ backgroundColor: style.color }} aria-label={`${style.label} color`} /></span>
                  <span role="cell">{style.label}</span>
                  <strong role="cell">{(Number(row.proportion) * 100).toFixed(2)}%</strong>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {selected && !hasPrivateFile && (
        <p className="panel-note">
          Metadata-only legacy imagery has no private source file, so preview
          and GeoAI processing are unavailable.
        </p>
      )}

      {selected?.metadata.registration_status === "READY" &&
        hasPrivateFile &&
        canProcess && (
          <div className="imagery-actions">
            <button
              type="button"
              className="primary-action"
              disabled={run.isPending || Boolean(activeJobId)}
              onClick={() => run.mutate("BUILDING_VECTORIZE")}
            >
              {activeJobKind === "BUILDING_VECTORIZE" && activeJobId
                ? "Building GeoAI running..."
                : "Run Building GeoAI"}
            </button>
            <button
              type="button"
              className="primary-action"
              disabled={run.isPending || Boolean(activeJobId)}
              onClick={() => run.mutate("ROAD_VECTORIZE")}
            >
              {activeJobKind === "ROAD_VECTORIZE" && activeJobId
                ? "Road GeoAI running..."
                : "Run Road GeoAI"}
            </button>
            <button
              type="button"
              className="primary-action"
              disabled={run.isPending || Boolean(activeJobId) || !landUseHasFourBands}
              onClick={() => run.mutate("LAND_USE_VECTORIZE")}
            >
              {activeJobKind === "LAND_USE_VECTORIZE" && activeJobId
                ? "Land-use GeoAI running..."
                : landUseHasFourBands
                  ? "Run Land-use GeoAI"
                  : "Land-use needs RGB+NIR"}
            </button>
            <button
              type="button"
              className="primary-action"
              disabled={run.isPending || Boolean(activeJobId)}
              onClick={() => run.mutate("PARCEL_DELINEATE")}
            >
              {activeJobKind === "PARCEL_DELINEATE" && activeJobId
                ? "Parcel candidates running..."
                : "Generate Plot Candidates"}
            </button>
          </div>
        )}

      {selected?.metadata.registration_status === "READY" && hasPrivateFile && canProcess && !landUseHasFourBands && (
        <p className="panel-note">
          Land-use GeoAI requires approximately 10 m, four-band RGB+NIR imagery. Building and road GeoAI remain available for this imagery.
        </p>
      )}

      {selected?.metadata.registration_status === "READY" &&
        hasPrivateFile &&
        !canProcess && (
          <p className="panel-note">
            `geoai:process` is required to run building, road, land-use, or parcel-candidate processing.
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
        preview. Building footprints, road vectors, land-use classes, and generated plot candidates are AI preliminary. Plot candidates must be verified against cadastral/FMB or survey evidence before legal use.
      </p>
      </section>
    </>
  );
}
