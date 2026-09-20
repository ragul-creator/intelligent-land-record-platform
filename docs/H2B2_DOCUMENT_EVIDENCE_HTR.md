# H.2B.2 Document evidence, OCR/HTR demo, and benchmarks

H.2B.2 closes the document-side demo gaps without changing the rule that OCR/HTR output is preliminary evidence until human review and validation.

## Implemented in this slice

- Side-by-side document source and OCR evidence in the Documents workspace.
- Private source preview through the existing short-lived signed source URL.
- OCR page text, page confidence, preprocessing metadata, model/engine metadata, and token-level evidence.
- Low-confidence token and extracted-field highlighting.
- Extracted-field provenance including page, source identifier, bounding box, OCR model, extractor version, and processing time.
- A model-agnostic benchmark runner at `ai/document_ai/benchmark.py`.
- Benchmark metrics include character error rate (CER), word error rate (WER), and structured-field exact-match accuracy when labeled ground truth is supplied.
- Per-sample failures are retained in the benchmark report instead of being converted into fake success.

## Benchmark manifest

Create a local, gitignored manifest beside the labeled sample files:

```json
{
  "version": 1,
  "samples": [
    {
      "id": "printed-tamil-01",
      "path": "printed-tamil-01.png",
      "category": "printed",
      "languages": "tam+eng",
      "ground_truth_text": "<verified transcription>",
      "expected_fields": {
        "survey_number": "123/4"
      }
    },
    {
      "id": "handwritten-01",
      "path": "handwritten-01.png",
      "category": "handwritten",
      "languages": "eng",
      "ground_truth_text": "<verified transcription>"
    }
  ]
}
```

Run:

```powershell
python -m ai.document_ai.benchmark data\benchmarks\document_ai\manifest.json --output data\processed\document_ai\benchmark-report.json
```

Do not commit private land records, benchmark source images, or generated reports unless they are explicitly synthetic/public demo fixtures.

## HTR status

The default asynchronous runtime remains pretrained Tesseract for printed Tamil + English. H.2B.2 now also includes an optional `TrOcrHtrEngine` backed by `microsoft/trocr-small-handwritten`, a pretrained TrOCR checkpoint fine-tuned on IAM English handwriting.

This TrOCR adapter is deliberately conservative:
- it supports `eng` only for this checkpoint and refuses Tamil or other requested languages instead of silently falling back;
- it loads `torch` / `transformers` lazily so the default backend image is not made heavyweight;
- it preserves the exact model identifier in `model_version`;
- it emits one full-line evidence region because this checkpoint recognizes a single text-line image rather than page layout;
- it leaves recognition confidence `null` instead of presenting generation scores as calibrated OCR confidence.

Install the optional HTR dependencies in the environment used for the benchmark:

```powershell
python -m pip install -r ai\document_ai\requirements-htr.txt
```

Run a labeled English handwriting benchmark:

```powershell
python -m ai.document_ai.benchmark data\benchmarks\document_ai\handwritten\manifest.json `
  --engine trocr `
  --output data\processed\document_ai\trocr-handwritten-benchmark.json
```

For a local/offline checkpoint, add:

```powershell
  --model-path "D:\path\to\trocr-small-handwritten"
```

### Public IAM smoke sample

A reproducible public smoke sample can use the IAM line image `a01-122-02.jpg` referenced by the official Hugging Face/Transformers TrOCR documentation. Keep the image and generated benchmark report local; do not commit dataset material unless its terms permit redistribution.

Example manifest after placing the image beside it:

```json
{
  "version": 1,
  "samples": [
    {
      "id": "iam-a01-122-02",
      "path": "a01-122-02.jpg",
      "category": "handwritten",
      "languages": "eng",
      "ground_truth_text": "industry, ' Mr. Brown commented icily. ' Let us have a"
    }
  ]
}
```

The benchmark report, not the model card, is the source of truth for this project's CER/WER. Do not copy published example output into the report as though it were a local model run.

The earlier `IndicOcrHtrEngine` remains an experimental optional adapter for a separately prepared gated IndicOCR checkout. It is not the primary H.2B.2 benchmark path because it requires separate upstream access/model preparation. It may still be evaluated later for Tamil handwriting, but Tamil HTR must not be claimed until a real labeled Tamil sample is run and measured.

### Recorded TrOCR benchmark result

A real local two-sample inference run was completed with:

- engine: `trocr-htr`
- engine version: `transformers 4.57.6`
- model: `microsoft/trocr-small-handwritten`
- category: `handwritten`
- language: English
- sample count: 2
- completed: 2
- failed: 0
- mean CER: `0.3`
- mean WER: `0.5`
- structured-field exact-match accuracy: `1.0` across one labeled structured field
- OCR confidence: `null` by design; no fabricated calibrated confidence is reported

Per-sample results:

- Synthetic handwritten-looking line, ground truth `Survey No. 123/4`: CER `0.0`, WER `0.0`, survey-number exact match `1.0`.
- Genuine human-written line, ground truth `Ragul`, recognized as `Royal`: CER `0.6`, WER `1.0`. This is a recorded failure case and shows that the pretrained checkpoint does not reliably recognize this handwriting style.

The synthetic sample demonstrates that the adapter and structured-field path execute end to end. The human-written sample is stronger evidence of actual handwriting behavior and must not be hidden by the perfect synthetic result. These two samples are still too small to claim representative handwriting accuracy.

Do not generalize these results to Tamil handwriting. The configured TrOCR checkpoint is English-only in this adapter, and Tamil HTR remains unbenchmarked.

## Remaining H.2B.2 closure

1. Run the new TrOCR unit tests plus the full Document AI suite.\n2. Run the pretrained TrOCR adapter on at least one labeled handwritten English sample and record the actual CER/WER.\n3. Add representative labeled Tamil + English benchmark samples (printed/degraded/mixed-language, plus the handwritten demo).\n4. Evaluate a credible Tamil handwriting model separately before claiming Tamil HTR support.\n5. Decide whether page-derivative/image endpoints are needed beyond the signed immutable-source viewer for the final demo contract.
