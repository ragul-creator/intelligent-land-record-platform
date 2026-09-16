"""Developer CLI for Phase C.1 GeoTIFF inspection and georeferenced tiling."""

from __future__ import annotations

import argparse
import json
import logging
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
    dataset_command = commands.add_parser("dataset-check", help="Validate a WHU image/mask split.")
    dataset_command.add_argument("--dataset-root", type=Path, required=True)
    dataset_command.add_argument("--split", choices=["train", "val", "test"], required=True)
    train_command = commands.add_parser("building-train", help="Train the C.2 binary building model.")
    train_command.add_argument("--dataset-root", type=Path, required=True)
    train_command.add_argument("--epochs", type=int, default=1)
    train_command.add_argument("--batch-size", type=int, default=2)
    train_command.add_argument("--learning-rate", type=float, default=1e-4)
    train_command.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    train_command.add_argument("--num-workers", type=int)
    train_command.add_argument("--limit", type=int)
    train_command.add_argument("--image-size", type=int)
    train_command.add_argument("--checkpoint-directory", type=Path, default=Path("data/models/buildings"))
    train_command.add_argument("--resume", type=Path)
    train_command.add_argument("--pretrained-backbone", action="store_true")
    infer_command = commands.add_parser("building-infer", help="Create pixel-space building predictions from a local checkpoint.")
    infer_command.add_argument("--checkpoint", type=Path, required=True)
    infer_command.add_argument("--input", type=Path, required=True)
    infer_command.add_argument("--output-dir", type=Path, required=True)
    infer_command.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    infer_command.add_argument("--threshold", type=float, default=0.5)
    evaluate_command = commands.add_parser("building-evaluate", help="Evaluate a local checkpoint on a WHU split.")
    evaluate_command.add_argument("--dataset-root", type=Path, required=True)
    evaluate_command.add_argument("--split", choices=["train", "val", "test"], default="val")
    evaluate_command.add_argument("--checkpoint", type=Path, required=True)
    evaluate_command.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    evaluate_command.add_argument("--batch-size", type=int, default=2)
    evaluate_command.add_argument("--num-workers", type=int)
    evaluate_command.add_argument("--limit", type=int)
    evaluate_command.add_argument("--image-size", type=int)
    vectorize_command = commands.add_parser("building-vectorize", help="Convert a building mask into preliminary GIS polygons.")
    vectorize_command.add_argument("--mask", type=Path, required=True)
    vectorize_command.add_argument("--source-raster", type=Path)
    vectorize_command.add_argument("--probability-mask", type=Path)
    vectorize_command.add_argument("--output", type=Path, required=True)
    vectorize_command.add_argument("--threshold", type=float, default=0.5)
    vectorize_command.add_argument("--min-area", type=float, default=0.0)
    vectorize_command.add_argument("--simplify-tolerance", type=float, default=0.0)
    vectorize_command.add_argument("--model-version", default="building-segmentation-c2-v1")
    vectorize_command.add_argument("--source-image")
    vectorize_command.add_argument("--source-mask")
    visualize_command = commands.add_parser("building-visualize", help="Draw C.3 building polygons over an aerial image for debugging.")
    visualize_command.add_argument("--image", type=Path, required=True)
    visualize_command.add_argument("--geojson", type=Path, required=True)
    visualize_command.add_argument("--output", type=Path, required=True)
    visualize_command.add_argument("--source-raster", type=Path)
    visualize_command.add_argument("--draw-labels", action="store_true")
    parcel_command = commands.add_parser("parcel-create", help="Create a source-backed preliminary parcel draft.")
    parcel_command.add_argument("--source-type", choices=["CADASTRAL_GIS", "FMB_IMPORT", "GNSS_SURVEY", "HUMAN_DRAWN", "AI_VISIBLE_BOUNDARY"], required=True)
    parcel_command.add_argument("--input", required=True, help="JSON/GeoJSON file path or literal JSON object.")
    parcel_command.add_argument("--output", type=Path, required=True)
    parcel_command.add_argument("--source-crs")
    parcel_command.add_argument("--source-reference")
    parcel_command.add_argument("--allow-multipolygon", action="store_true")
    parcel_command.add_argument("--model-version")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "inspect":
            print(json.dumps(inspect_raster(arguments.source).to_dict(), indent=2, sort_keys=True))
            return 0
        if arguments.command == "tile":
            summary = tile_geotiff(arguments.source, tile_size=arguments.tile_size, output_directory=arguments.output_directory, overwrite=arguments.overwrite)
            print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
            return 0
        if arguments.command == "dataset-check":
            from ai.geoai.segmentation.building_dataset import validate_whu_dataset

            check, _ = validate_whu_dataset(arguments.dataset_root, arguments.split)
            print(json.dumps(check.to_dict(), indent=2, sort_keys=True))
            return 0
        if arguments.command == "building-train":
            from ai.geoai.segmentation.pipeline import TrainingConfig, train

            result = train(
                TrainingConfig(
                    dataset_root=arguments.dataset_root,
                    epochs=arguments.epochs,
                    batch_size=arguments.batch_size,
                    learning_rate=arguments.learning_rate,
                    device=arguments.device,
                    num_workers=arguments.num_workers,
                    limit=arguments.limit,
                    image_size=arguments.image_size,
                    checkpoint_directory=arguments.checkpoint_directory,
                    resume=arguments.resume,
                    pretrained_backbone=arguments.pretrained_backbone,
                )
            )
            print(json.dumps(result, indent=2, default=str, sort_keys=True))
            return 0
        if arguments.command == "building-infer":
            from ai.geoai.segmentation.pipeline import infer_input

            print(json.dumps(infer_input(arguments.checkpoint, arguments.input, arguments.output_dir, device_request=arguments.device, threshold=arguments.threshold), indent=2, sort_keys=True))
            return 0
        if arguments.command == "building-vectorize":
            from ai.geoai.polygonization.buildings import VectorizationConfig, vectorize_from_paths, write_geojson

            result = vectorize_from_paths(
                arguments.mask,
                source_raster=arguments.source_raster,
                probability_mask_path=arguments.probability_mask,
                config=VectorizationConfig(
                    threshold=arguments.threshold,
                    min_area=arguments.min_area,
                    simplify_tolerance=arguments.simplify_tolerance,
                ),
                model_version=arguments.model_version,
                source_image=arguments.source_image,
                source_mask=arguments.source_mask,
            )
            output = write_geojson(result, arguments.output)
            print(
                json.dumps(
                    {
                        "output": str(output),
                        "feature_count": len(result.features),
                        "coordinate_space": result.coordinate_space,
                        "source_crs": result.source_crs,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if arguments.command == "building-visualize":
            from ai.geoai.polygonization.visualization import render_building_overlay

            output = render_building_overlay(
                arguments.image,
                arguments.geojson,
                arguments.output,
                source_raster=arguments.source_raster,
                draw_labels=arguments.draw_labels,
            )
            print(json.dumps({"output": str(output)}, indent=2, sort_keys=True))
            return 0
        if arguments.command == "parcel-create":
            from ai.geoai.parcels.acquisition import create_parcel, load_json_input
            from ai.geoai.parcels.export import write_parcel_geojson

            parcel = create_parcel(
                arguments.source_type,
                load_json_input(arguments.input),
                source_crs=arguments.source_crs,
                source_reference=arguments.source_reference,
                allow_multipolygon=arguments.allow_multipolygon,
                model_version=arguments.model_version,
            )
            output = write_parcel_geojson(parcel, arguments.output)
            print(
                json.dumps(
                    {
                        "output": str(output),
                        "parcel_id": parcel.parcel_id,
                        "status": parcel.status,
                        "coordinate_space": parcel.coordinate_space,
                        "requires_survey": parcel.requires_survey,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        from ai.geoai.segmentation.pipeline import evaluate

        print(
            json.dumps(
                evaluate(
                    arguments.dataset_root,
                    arguments.split,
                    arguments.checkpoint,
                    device_request=arguments.device,
                    batch_size=arguments.batch_size,
                    num_workers=arguments.num_workers,
                    limit=arguments.limit,
                    image_size=arguments.image_size,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except RasterValidationError as error:
        print(
            json.dumps({"error": {"code": "RASTER_VALIDATION_FAILED", "messages": error.errors}}),
            file=sys.stderr,
        )
        return 2
    except (FileExistsError, ValueError, RuntimeError) as error:
        if arguments.command == "tile":
            code = "TILING_FAILED"
        elif arguments.command == "building-vectorize":
            code = "VECTORIZATION_FAILED"
        elif arguments.command == "building-visualize":
            code = "VISUALIZATION_FAILED"
        elif arguments.command == "parcel-create":
            code = "PARCEL_ACQUISITION_FAILED"
        else:
            code = "SEGMENTATION_FAILED"
        print(json.dumps({"error": {"code": code, "message": str(error)}}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
