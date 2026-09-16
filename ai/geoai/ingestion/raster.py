"""Validated, typed inspection for georeferenced GeoTIFF inputs."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.errors import RasterioError


class RasterValidationError(ValueError):
    """A clear, caller-safe raster validation failure."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class RasterBounds:
    left: float
    bottom: float
    right: float
    top: float


@dataclass(frozen=True)
class RasterTransform:
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    @classmethod
    def from_affine(cls, transform: Affine) -> "RasterTransform":
        return cls(transform.a, transform.b, transform.c, transform.d, transform.e, transform.f)


@dataclass(frozen=True)
class RasterMetadata:
    filename: str
    driver: str
    width: int
    height: int
    band_count: int
    dtypes: tuple[str, ...]
    crs_wkt: str | None
    epsg: int | None
    is_projected: bool
    is_geographic: bool
    bounds: RasterBounds
    transform: RasterTransform
    resolution_x: float
    resolution_y: float
    nodata: float | int | str | None
    estimated_uncompressed_size_bytes: int

    def to_dict(self) -> dict[str, object]:
        """Return JSON-ready metadata without exposing Rasterio implementation objects."""
        return asdict(self)


def _validate_dataset(dataset: rasterio.io.DatasetReader, require_crs: bool) -> list[str]:
    errors: list[str] = []
    if dataset.driver != "GTiff":
        errors.append(f"Expected a GeoTIFF (GTiff) driver, found {dataset.driver!r}.")
    if dataset.width <= 0 or dataset.height <= 0 or dataset.count <= 0:
        errors.append("Raster dimensions and band count must be greater than zero.")
    if require_crs and dataset.crs is None:
        errors.append("Raster CRS is required but missing.")

    transform = dataset.transform
    coefficients = (transform.a, transform.b, transform.c, transform.d, transform.e, transform.f)
    if not all(math.isfinite(value) for value in coefficients):
        errors.append("Raster affine transform contains non-finite values.")
    elif transform == Affine.identity() or math.isclose(transform.determinant, 0.0):
        errors.append("Raster affine transform is not a valid georeferencing transform.")

    bounds = dataset.bounds
    if not all(math.isfinite(value) for value in (bounds.left, bounds.bottom, bounds.right, bounds.top)):
        errors.append("Raster bounds contain non-finite values.")
    elif bounds.left >= bounds.right or bounds.bottom >= bounds.top:
        errors.append("Raster bounds are invalid or empty.")
    if not all(math.isfinite(value) and value > 0 for value in dataset.res):
        errors.append("Raster pixel resolution must be finite and greater than zero.")
    return errors


def inspect_raster(path: str | Path, *, require_crs: bool = True) -> RasterMetadata:
    """Open, validate, and describe a GeoTIFF without leaking Rasterio stack traces."""
    source = Path(path)
    if not source.is_file():
        raise RasterValidationError([f"Raster file does not exist: {source}"])
    try:
        with rasterio.open(source) as dataset:
            errors = _validate_dataset(dataset, require_crs)
            if errors:
                raise RasterValidationError(errors)
            crs = dataset.crs
            dtypes = tuple(str(dtype) for dtype in dataset.dtypes)
            estimated_size = dataset.width * dataset.height * sum(np.dtype(dtype).itemsize for dtype in dtypes)
            return RasterMetadata(
                filename=source.name,
                driver=dataset.driver,
                width=dataset.width,
                height=dataset.height,
                band_count=dataset.count,
                dtypes=dtypes,
                crs_wkt=crs.to_wkt() if crs is not None else None,
                epsg=crs.to_epsg() if crs is not None else None,
                is_projected=bool(crs and crs.is_projected),
                is_geographic=bool(crs and crs.is_geographic),
                bounds=RasterBounds(*dataset.bounds),
                transform=RasterTransform.from_affine(dataset.transform),
                resolution_x=float(dataset.res[0]),
                resolution_y=float(dataset.res[1]),
                nodata=dataset.nodata,
                estimated_uncompressed_size_bytes=estimated_size,
            )
    except RasterValidationError:
        raise
    except (OSError, RasterioError) as error:
        raise RasterValidationError([f"Raster could not be read: {source.name}"]) from error
