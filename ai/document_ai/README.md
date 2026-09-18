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

## Structured Field Extraction: Phase F.2

F.2 consumes the vendor-neutral `DocumentOcrResult` from F.1 and produces evidence-grounded, **preliminary** land-record field candidates. It never edits OCR results or original documents, and it never invents a survey, khasra, khata, or registration identifier when source evidence is absent or ambiguous.

The canonical fields are `survey_number`, `khasra_number`, `khata_number`, `owner_details`, `plot_area`, `village`, `tehsil`, `district`, `land_classification`, `mutation_records`, and `registration_information`. Every result includes every canonical field as a candidate list; an unavailable field remains an empty list rather than a fabricated value. Repeated or conflicting candidates are retained with their own evidence for F.3 to assess later.

The deterministic extractor currently recognizes the demonstrated Tamil and English label dictionaries, including `Survey No`, `Owner`, `Area`, `Village`, `Tehsil`/`Taluk`, `District`, `Mutation`, and `Registration`, as well as `சர்வே எண்`, `உரிமையாளர்`, `பரப்பளவு`, `கிராமம்`, `மாவட்டம்`, `தாலுகா`, and `வட்டம்`. The dictionary is modular; it is not a claim that terminology is complete or universally correct across Indian jurisdictions and languages.

Candidates preserve the source-language `original_value`, a deterministic `normalized_value`, extraction-stage confidence, source ID, page number, value-token bounding box when available, OCR model version, extractor version, and UTC timestamp. Text normalization only collapses ordinary whitespace. Identifiers retain meaningful letters, digits, `/`, and `-`; areas with an explicit unit become objects such as `{ "value": 1200, "unit": "sq_ft" }`, including Tamil `சதுர அடி`. Mutation and registration details remain repeatable entry structures, with only unambiguous ISO-like dates normalized.

F.2 confidence combines available OCR token confidence with strong label, proximity, and normalization signals. It is `null` when value-token OCR confidence is unavailable and is **not** an F.3 record-confidence, validation threshold, conflict decision, or review-routing signal. F.3 owns validation/review decisions; F.4 owns persistence, Celery, backend APIs, and UI integration.

Use the library boundary for later orchestration:

```python
from ai.document_ai.extraction import extract_land_record_fields

extraction_result = extract_land_record_fields(document_ocr_result)
```

## Document Validation: Phase F.3

F.3 is pure preliminary decision support. It validates F.2 field candidates through the shared `ai.validation` E.1 contracts and produces an existing `ValidationReport`, a conservative document confidence summary, check metadata, and a non-persisted DOCUMENT review recommendation. It does not determine legal validity, publish records, correct values, call a government system, create a database row, or create an E.2 review task.

F.2 candidate provenance maps directly to E.1 `EvidenceReference`: `source_type` is `DOCUMENT_OCR`, `source_id` and page are retained, and a bounding box becomes `(left, top, width, height)` only when F.2 supplied one. Field original/normalized values, extraction confidence, OCR/extractor versions, candidate reference, and rule metadata remain attached to issues for future correction capture.

`DocumentValidationPolicy` is caller-configurable. Its `required_fields`, identifier patterns, confidence policy, unknown-confidence handling, duplicate detector, master-data verifier, and blocking-field choices are MVP implementation settings, not statutory requirements. The default confidence bands are `HIGH >= 0.90`, `MEDIUM >= 0.75`, and `LOW < 0.75`; these are hackathon defaults, never legal thresholds. A low candidate confidence creates `LOW_CONFIDENCE`; unknown confidence creates `UNKNOWN_CONFIDENCE` and review by default unless the caller explicitly disables that behavior.

Candidates for a required field must be non-empty. Missing configured fields create `REQUIRED_FIELD_MISSING` without inventing a candidate. Different normalized candidates create `FIELD_VALUE_CONFLICT` while retaining all evidence; Tamil/English values are not translated or silently declared equivalent. Identifier checks are syntax-only, and area checks require a positive value plus a supported canonical unit. Neither check confirms a governmental/legal identifier or reconciles area with GIS evidence.

Optional E.1 `DuplicateDetector` and `MasterDataVerifier` adapters can be passed through the policy. Without either adapter, their check metadata is explicitly `NOT_PERFORMED`; F.3 never claims that a duplicate or master-data database was checked. Configured adapter results preserve external candidate IDs, scores, reasons, source names, and references. Failed verification requires review but never overwrites OCR evidence.

Document confidence is the minimum representative confidence among populated fields with known confidence, so a low candidate cannot be hidden by a higher one. It is `null` when confidence is unknown, required configured fields are missing, or no usable evidence is available. Missing/conflicting field counts remain explicit in the summary; this is not legal certainty or final record confidence.

The review recommendation maps E.1 `INFO/LOW/MEDIUM/HIGH` directly to E.2-compatible DOCUMENT severities. Missing required fields, conflicting/malformed official identifiers, invalid required area, and failed configured blocking verification are counted as blocking. Low confidence recommends review but is non-blocking by default. F.4 will later persist this draft with the existing E.2 `create_review_task(...)` flow; reviewer actions and automatic correction remain outside F.3.

Use the pure library boundary after F.2:

```python
from ai.document_ai.extraction import extract_land_record_fields
from ai.document_ai.validation import validate_document_extraction

extraction_result = extract_land_record_fields(document_ocr_result)
validation_result = validate_document_extraction(extraction_result)
```
