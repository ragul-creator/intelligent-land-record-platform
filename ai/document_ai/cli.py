"""Local-only CLI for a preliminary F.1 OCR smoke test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai.document_ai.errors import DocumentAiError
from ai.document_ai.languages import parse_language_configuration
from ai.document_ai.pipeline import OcrPipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run preliminary configurable-language OCR on one local document.")
    parser.add_argument("input", type=Path, help="Local PDF, PNG, JPG/JPEG, TIFF, or TIF source document.")
    parser.add_argument("--source-id", help="Stable source file identifier for later provenance tracking.")
    parser.add_argument("--languages", default="tam+eng", help="Tesseract language codes, for example tam+eng, hin+eng, or tel+eng.")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--output", type=Path, help="Optional JSON output path; the source input is never overwritten.")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.output and arguments.output.expanduser().resolve() == arguments.input.expanduser().resolve():
            raise ValueError("OCR output must not overwrite the original input document.")
        result = OcrPipeline(dpi=arguments.dpi).process(
            arguments.input,
            source_id=arguments.source_id,
            languages=parse_language_configuration(arguments.languages),
        )
        output = json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return 0
    except (DocumentAiError, OSError, ValueError) as error:
        print(json.dumps({"error": {"code": "DOCUMENT_OCR_FAILED", "message": str(error)}}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
