"""Developer CLI for Phase C.1 GeoTIFF inspection and georeferenced tiling."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai.geoai.ingestion.raster import RasterValidationError, inspect_raster
from ai.geoai.tiling.raster_tiler import tile_geotiff


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and tile validated GeoTIFF inputs.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_command = commands.add_parser("inspect", help="Print validated raster metadata as JSON.")
    inspect_command.add_argument("source", type=Path)
    tile_command = commands.add_parser("tile", help="Write CRS-preserving GeoTIFF tiles.")
    tile_command.add_argument("source", type=Path)
    tile_command.add_argument("--tile-size", type=int, default=512)
    tile_command.add_argument("--output-directory", type=Path)
    tile_command.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "inspect":
            print(json.dumps(inspect_raster(arguments.source).to_dict(), indent=2, sort_keys=True))
            return 0
        summary = tile_geotiff(
            arguments.source,
            tile_size=arguments.tile_size,
            output_directory=arguments.output_directory,
            overwrite=arguments.overwrite,
        )
        print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
        return 0
    except RasterValidationError as error:
        print(
            json.dumps({"error": {"code": "RASTER_VALIDATION_FAILED", "messages": error.errors}}),
            file=sys.stderr,
        )
        return 2
    except (FileExistsError, ValueError) as error:
        print(json.dumps({"error": {"code": "TILING_FAILED", "message": str(error)}}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
