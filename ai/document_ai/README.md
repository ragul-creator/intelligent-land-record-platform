# Document AI: Phase F.1

Phase F.1 converts local PDF and image documents into **preliminary OCR output**. It does not validate a land record, infer structured fields, persist data, call government systems, or provide an API/UI workflow.

## Supported inputs and preprocessing

The local pipeline accepts PDF, PNG, JPG/JPEG, TIFF, and TIF files. PDF pages are rendered in deterministic source order at a configurable DPI (default `300`). Images are orientation-normalized, converted to grayscale, contrast-enhanced, gently denoised, and deskewed only when a modest reliable angle is found. Thresholding is optional because it can remove faint record details.

The original source is only opened and copied into memory. It is never overwritten and no intermediate page images are retained. The result has a caller-supplied `source_id` when available, one-based `page_number`, page dimensions, applied preprocessing operations, token bounding boxes, normalized OCR confidence, engine/version metadata, and UTC timestamps. Results carry `OCR_PRELIMINARY`; OCR confidence is a machine recognition signal from `0.0` to `1.0`, not a legal, field-validation, or record-confidence decision. Engines without confidence data return `null`.

## OCR engine and language configuration

F.1 uses a lazy, swappable [Tesseract](https://tesseract-ocr.github.io/) adapter for pretrained printed-text OCR. The platform architecture accepts configurable installed Tesseract language packs rather than a Tamil/English allowlist, and it supplies word-level bounding boxes and confidences. Tesseract is loaded only when an OCR call is made, so imports and unit tests do not require it.

Install Python dependencies from the repository root:

```powershell
python -m pip install -r ai/document_ai/requirements.txt
```

Tamil + English (`tam+eng`) is the currently tested/demo configuration for the Tamil Nadu hackathon scenario. Configurations such as `hin+eng`, `tel+eng`, `kan+eng`, `mal+eng`, `ben+eng`, `mar+eng`, `guj+eng`, `pan+eng`, and `ori+eng` are accepted when the corresponding pretrained Tesseract `traineddata` files are installed. They must be evaluated separately: this project does not claim measured OCR accuracy or production-quality behavior for languages it has not evaluated.

Install the native Tesseract executable separately and ensure it is on `PATH`; install each requested language pack, including `eng` and `tam` for the demonstrated setup. On Windows, configure the executable path through the `TesseractOcrEngine(command=...)` constructor if it is not on `PATH`. The adapter exposes `available_languages()` to list installed packs, validates safe lower-case traineddata identifiers, and fails clearly if a requested pack is unavailable. It never silently changes any requested language configuration into English-only OCR.

CPU is the expected F.1 baseline. This adapter does not require CUDA and makes no OCR model downloads. Standard printed text is the current baseline; handwritten records in any language may receive poor or empty recognition and must remain reviewable. A future handwriting-capable pretrained HTR engine can implement the `OcrEngine` protocol without changing the pipeline.

## Local smoke test

After the Python and system prerequisites are installed, run one document locally. This writes only generated JSON beneath the ignored processed-data directory:

```powershell
python -m ai.document_ai.cli "<path-to-record.pdf>" --source-id "local-smoke-record" --languages tam+eng --dpi 300 --output data/processed/document_ai/local-smoke.json
```

Use `--languages hin+eng` or `--languages tel+eng` only after installing and separately evaluating those packs; use `--languages eng` for an explicitly English-only document. Result metadata retains the exact `requested_languages` separately from `project_tested_languages` (`tam`, `eng`) and engine/model information. No language is reported as detected because F.1 does not implement language detection. The output flows into Phase F.2 structured-field extraction later; it is not authoritative land-record data.

The CLI serializes output with `ensure_ascii=False` and writes UTF-8 explicitly. When inspecting a JSON file in legacy Windows PowerShell, force UTF-8 rather than letting a code-page reader treat UTF-8 bytes as ANSI text:

```powershell
Get-Content -Raw -Encoding utf8 data/processed/document_ai/local-smoke.json
```

## Deliberate deferrals

F.1 does not implement handwriting training, structured field extraction, document validation, persistence, Celery jobs, backend endpoints, user workflows, or any statutory/legal determination. Those concerns remain in later Phase F work.
