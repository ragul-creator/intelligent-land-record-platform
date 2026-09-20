"""Benchmark runner for printed OCR and pluggable HTR engines.

The harness never fabricates accuracy. Metrics are emitted only when a manifest
contains ground-truth text or expected fields.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean
from typing import Any

from ai.document_ai.extraction import extract_land_record_fields
from ai.document_ai.languages import parse_language_configuration, validate_language_codes
from ai.document_ai.ocr.base import OcrEngine
from ai.document_ai.pipeline import OcrPipeline


def _edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, start=1):
        current = [row]
        for column, observed in enumerate(hypothesis, start=1):
            substitution = previous[column - 1] + (expected != observed)
            insertion = current[column - 1] + 1
            deletion = previous[column] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref = list(reference)
    if not ref:
        return 0.0 if not hypothesis else 1.0
    return _edit_distance(ref, list(hypothesis)) / len(ref)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref = reference.split()
    if not ref:
        return 0.0 if not hypothesis.split() else 1.0
    return _edit_distance(ref, hypothesis.split()) / len(ref)


def _normal(value: object) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value).strip().casefold().split())


def _field_metrics(result, expected: dict[str, Any]) -> tuple[int, int, dict[str, Any]]:
    matched = 0
    total = len(expected)
    details: dict[str, Any] = {}
    for field_name, expected_value in expected.items():
        candidates = result.fields.get(field_name, ())
        observed = [candidate.normalized_value if candidate.normalized_value is not None else candidate.original_value for candidate in candidates]
        is_match = any(_normal(value) == _normal(expected_value) for value in observed)
        matched += int(is_match)
        details[field_name] = {
            "expected": expected_value,
            "observed": observed,
            "exact_match": is_match,
        }
    return matched, total, details


def evaluate_manifest(
    manifest_path: str | Path,
    *,
    engine: OcrEngine | None = None,
) -> dict[str, Any]:
    """Evaluate a labeled manifest with no hidden fallback or fake success."""

    manifest_path = Path(manifest_path).expanduser().resolve()
    root = manifest_path.parent
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        samples = payload
    elif isinstance(payload, dict):
        samples = payload.get("samples")
    else:
        samples = None
    if not isinstance(samples, list):
        raise ValueError("Benchmark manifest must be a list or contain a 'samples' list.")

    results: list[dict[str, Any]] = []
    cer_values: list[float] = []
    wer_values: list[float] = []
    field_matches = 0
    field_total = 0

    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ValueError(f"Benchmark sample {index} must be an object.")
        sample_id = str(sample.get("id") or f"sample-{index + 1}")
        source = (root / str(sample["path"])).resolve()
        try:
            source.relative_to(root)
        except ValueError as error:
            raise ValueError(f"Benchmark sample {sample_id} escapes the manifest directory.") from error

        raw_languages = sample.get("languages", "tam+eng")
        languages = (
            parse_language_configuration(raw_languages)
            if isinstance(raw_languages, str)
            else validate_language_codes(tuple(raw_languages))
        )

        row: dict[str, Any] = {
            "id": sample_id,
            "category": sample.get("category", "unspecified"),
            "languages": list(languages),
            "source": str(source.relative_to(root)),
            "status": "FAILED",
            "error": None,
            "character_error_rate": None,
            "word_error_rate": None,
            "field_exact_match": None,
            "field_details": {},
        }
        try:
            ocr = OcrPipeline(engine=engine).process(
                source,
                source_id=sample_id,
                languages=languages,
                allowed_root=root,
            )
            hypothesis = "\n".join(page.text for page in ocr.pages).strip()
            reference = sample.get("ground_truth_text")
            if isinstance(reference, str):
                cer = character_error_rate(reference, hypothesis)
                wer = word_error_rate(reference, hypothesis)
                row["character_error_rate"] = cer
                row["word_error_rate"] = wer
                cer_values.append(cer)
                wer_values.append(wer)

            expected_fields = sample.get("expected_fields")
            if isinstance(expected_fields, dict):
                extraction = extract_land_record_fields(ocr)
                matched, total, details = _field_metrics(extraction, expected_fields)
                row["field_exact_match"] = matched / total if total else None
                row["field_details"] = details
                field_matches += matched
                field_total += total

            row.update(
                {
                    "status": "COMPLETED",
                    "engine": ocr.engine,
                    "engine_version": ocr.engine_version,
                    "model_version": ocr.model_version,
                    "page_count": len(ocr.pages),
                    "ocr_confidence": (
                        fmean(page.confidence for page in ocr.pages if page.confidence is not None)
                        if any(page.confidence is not None for page in ocr.pages)
                        else None
                    ),
                }
            )
        except Exception as error:  # benchmark failures are evidence too
            row["error"] = f"{type(error).__name__}: {error}"
        results.append(row)

    return {
        "manifest_version": payload.get("version", 1) if isinstance(payload, dict) else 1,
        "sample_count": len(results),
        "completed_count": sum(result["status"] == "COMPLETED" for result in results),
        "failed_count": sum(result["status"] == "FAILED" for result in results),
        "categories": sorted({str(result["category"]) for result in results}),
        "mean_character_error_rate": fmean(cer_values) if cer_values else None,
        "mean_word_error_rate": fmean(wer_values) if wer_values else None,
        "field_exact_match_accuracy": field_matches / field_total if field_total else None,
        "field_ground_truth_count": field_total,
        "samples": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate labeled Document AI OCR/HTR samples.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--engine",
        choices=("tesseract", "trocr", "indic-ocr"),
        default="tesseract",
        help="Recognition engine. TrOCR is optional English HTR; IndicOCR requires a prepared local gated-model checkout.",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        help="Optional local model path. Required for IndicOCR; for TrOCR it overrides the default Hugging Face checkpoint.",
    )
    args = parser.parse_args()

    engine = None
    if args.engine == "trocr":
        from ai.document_ai.ocr.trocr_htr import DEFAULT_TROCR_HANDWRITTEN_MODEL, TrOcrHtrEngine

        engine = TrOcrHtrEngine(
            model_name_or_path=args.model_path or DEFAULT_TROCR_HANDWRITTEN_MODEL,
        )
    elif args.engine == "indic-ocr":
        if args.model_path is None:
            parser.error("--model-path is required when --engine indic-ocr is selected.")
        from ai.document_ai.ocr.indic_ocr_htr import IndicOcrHtrEngine

        engine = IndicOcrHtrEngine(model_path=args.model_path)

    report = evaluate_manifest(args.manifest, engine=engine)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
