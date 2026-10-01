export type SavedWebGisDemoCardId = "vegas-geoai" | "tamilnadu-lulc";

type DemoCard = {
  id: SavedWebGisDemoCardId;
  title: string;
  filename: string;
  previewUrl: string;
  description: string;
  metrics: string[];
};

const DEMO_CARDS: DemoCard[] = [
  {
    id: "vegas-geoai",
    title: "Vegas building, road & plot demo",
    filename: "vegas_building_test.tif",
    previewUrl: "/demo/webgis-vegas-preview.png",
    description:
      "The exact tested GeoTIFF with its persisted Building, SAM-Road, and preliminary plot outputs.",
    metrics: [
      "29 building footprints",
      "9 road centerlines",
      "42 plot candidates",
      "0 road topology disagreements",
    ],
  },
  {
    id: "tamilnadu-lulc",
    title: "RGB+NIR land-use demo",
    filename: "ESA_WorldCover_10m_2021_v200_N09E080_S2RGBNIR.tif",
    previewUrl: "/demo/webgis-lulc-preview.png",
    description:
      "The tested four-band RGB+NIR GeoTIFF preview with the persisted SegFormer-B2 land-use classification output.",
    metrics: [
      "9 land-use class regions",
      "4 source bands (RGB+NIR)",
      "SegFormer-B2",
    ],
  },
];

export function SavedWebGisDemoPanel({
  activeId,
  loading,
  error,
  onSelect,
  onClear,
}: {
  activeId: SavedWebGisDemoCardId | null;
  loading: boolean;
  error: boolean;
  onSelect: (id: SavedWebGisDemoCardId) => void;
  onClear: () => void;
}) {
  return (
    <section className="saved-webgis-panel" aria-label="Saved WebGIS demonstrations">
      <div className="saved-webgis-heading">
        <div>
          <p className="eyebrow">Saved GeoAI demonstrations · actual tested runs</p>
          <h2>Open a tested GeoTIFF with its model output</h2>
          <p>
            These are preserved inputs and outputs from the Bhumi-AI runs used during testing.
            Open one to display its imagery and stored model vectors directly on the WebGIS map.
            No model rerun is required.
          </p>
        </div>
        {activeId && (
          <button type="button" onClick={onClear}>
            Return to live project layers
          </button>
        )}
      </div>

      <div className="saved-webgis-grid">
        {DEMO_CARDS.map((card) => {
          const active = activeId === card.id;
          return (
            <article className={`saved-webgis-card${active ? " active" : ""}`} key={card.id}>
              <img src={card.previewUrl} alt={`${card.title} source imagery preview`} loading="lazy" />
              <div className="saved-webgis-card-body">
                <div className="saved-webgis-card-title">
                  <strong>{card.title}</strong>
                  {active && <span>Displayed on map</span>}
                </div>
                <small>{card.filename}</small>
                <p>{card.description}</p>
                <div className="saved-webgis-metrics" aria-label={`${card.title} saved results`}>
                  {card.metrics.map((metric) => <span key={metric}>{metric}</span>)}
                </div>
                <button
                  type="button"
                  className="primary-action"
                  disabled={active && loading}
                  onClick={() => onSelect(card.id)}
                >
                  {active && loading
                    ? `Loading saved run — ${card.title}`
                    : active
                      ? `Reload saved map — ${card.title}`
                      : `Open ${card.title} — Open saved map`}
                </button>
                {active && error && (
                  <p className="error-copy" role="alert">
                    The preserved WebGIS result could not be loaded.
                  </p>
                )}
              </div>
            </article>
          );
        })}
      </div>

      {activeId && (
        <p className="demo-fixture-note">
          Preserved SIH walkthrough evidence uses the exact persisted GeoAI run and its source imagery preview.
          Preliminary — survey/FMB verification required. Building footprints and AI plot candidates are review evidence,
          not statutory property boundaries.
        </p>
      )}
    </section>
  );
}
