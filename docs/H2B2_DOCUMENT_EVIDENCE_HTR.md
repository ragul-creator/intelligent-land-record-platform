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

The default asynchronous runtime remains pretrained Tesseract for printed Tamil + English. An optional `IndicOcrHtrEngine` adapter now targets a separately prepared local checkout of Bodhan AI / AI4Bharat IndicOCR. The upstream model card documents printed recognition for English plus 22 Indian languages and handwriting recognition for English plus 12 Indian languages, including Tamil. Its handwriting quality is explicitly described upstream as work in progress.

The adapter is deliberately lazy:
- it does not download gated weights;
- it does not add heavyweight model dependencies to the default backend image;
- it refuses requested handwriting languages outside the upstream documented set instead of silently falling back;
- upstream block `conf` is layout-detection confidence, so this platform does **not** relabel it as OCR/transcription confidence. HTR recognition confidence remains `null` unless the recognizer exposes a genuine transcription confidence.

The real model is **not yet the default asynchronous backend engine**. Until the model is locally installed, a labeled handwritten sample is run through it, and the resulting CER/WER is recorded, handwritten pages must not be presented as benchmarked production-quality HTR.

After accepting the upstream model access/license terms separately and preparing its local checkout, benchmark with:

```powershell
python -m ai.document_ai.benchmark data\benchmarks\document_ai\manifest.json `
  --engine indic-ocr `
  --model-path "<LOCAL_INDIC_OCR_CHECKOUT>" `
  --output data\processed\document_ai\indic-ocr-benchmark.json
```

## Remaining H.2B.2 closure

1. Run the new frontend and Document AI tests.
2. Prepare the optional IndicOCR model locally and benchmark it on at least one labeled handwritten Tamil or English demo sample.
3. Add representative labeled Tamil + English benchmark samples (printed/degraded/mixed-language, plus the handwritten demo).
4. Record actual CER/WER and field exact-match numbers; do not invent benchmark results.
5. Decide whether page-derivative/image endpoints are needed beyond the signed immutable-source viewer for the final demo contract.
