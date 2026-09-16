"""CRS-preserving GeoTIFF tiling using Rasterio windows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import rasterio
from rasterio.windows import Window, bounds as window_bounds, transform as window_transform

from ai.geoai.ingestion.raster import RasterBounds, RasterMetadata, RasterTransform, inspect_raster


@dataclass(frozen=True)
class TileMetadata:
    path: Path
    row: int
    column: int
    width: int
    height: int
    bounds: RasterBounds
    transform: RasterTransform

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["path"] = str(self.path)
        return result


@dataclass(frozen=True)
class TileRunSummary:
    source: Path
    output_directory: Path
    tile_size: int
    source_metadata: RasterMetadata
    tiles: tuple[TileMetadata, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source": str(self.source),
            "output_directory": str(self.output_directory),
            "tile_size": self.tile_size,
            "source_crs": self.source_metadata.crs_wkt,
            "source_dimensions": {"width": self.source_metadata.width, "height": self.source_metadata.height},
            "tile_count": len(self.tiles),
            "tiles": [tile.to_dict() for tile in self.tiles],
        }


def default_output_directory(source: str | Path) -> Path:
    """Return the repository-relative Phase C.1 tile location for a source raster."""
    return Path("data/processed/geoai/tiles") / Path(source).stem


def tile_geotiff(
    source: str | Path,
    *,
    tile_size: int = 512,
    output_directory: str | Path | None = None,
    overwrite: bool = False,
) -> TileRunSummary:
    """Split a validated GeoTIFF into deterministic, georeferenced, unpadded tiles."""
    if tile_size <= 0:
        raise ValueError("Tile size must be greater than zero.")
    source_path = Path(source)
    source_metadata = inspect_raster(source_path)
    destination = Path(output_directory) if output_directory is not None else default_output_directory(source_path)
    destination.mkdir(parents=True, exist_ok=True)
    tiles: list[TileMetadata] = []

    with rasterio.open(source_path) as dataset:
        for row_index, row_offset in enumerate(range(0, dataset.height, tile_size)):
            for column_index, column_offset in enumerate(range(0, dataset.width, tile_size)):
                width = min(tile_size, dataset.width - column_offset)
                height = min(tile_size, dataset.height - row_offset)
                window = Window(column_offset, row_offset, width, height)
                tile_path = destination / f"{source_path.stem}_r{row_index:04d}_c{column_index:04d}.tif"
                if tile_path.exists() and not overwrite:
                    raise FileExistsError(f"Tile already exists: {tile_path}")
                child_transform = window_transform(window, dataset.transform)
                profile = dataset.profile.copy()
                profile.update(
                    driver="GTiff",
                    width=width,
                    height=height,
                    transform=child_transform,
                    crs=dataset.crs,
                    nodata=dataset.nodata,
                )
                with rasterio.open(tile_path, "w", **profile) as tile_dataset:
                    tile_dataset.write(dataset.read(window=window))
                left, bottom, right, top = window_bounds(window, dataset.transform)
                tiles.append(
                    TileMetadata(
                        path=tile_path,
                        row=row_index,
                        column=column_index,
                        width=width,
                        height=height,
                        bounds=RasterBounds(left, bottom, right, top),
                        transform=RasterTransform.from_affine(child_transform),
                    )
                )
    return TileRunSummary(source_path, destination, tile_size, source_metadata, tuple(tiles))
