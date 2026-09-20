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

The current default runtime remains pretrained Tesseract for printed Tamil + English. The benchmark harness accepts any engine implementing the existing `OcrEngine` protocol, so a pretrained handwriting engine can be evaluated without changing the evidence or extraction contracts.

A real handwriting model is **not yet wired into the asynchronous backend worker** in this slice. Until that integration is completed and benchmarked, handwritten pages must not be presented as production-quality HTR. Low/unknown-confidence handwriting should remain review-required.

## Remaining H.2B.2 closure

1. Run the new frontend and Document AI tests.
2. Add a real pretrained HTR adapter and benchmark it on at least one labeled handwritten demo sample.
3. Add representative labeled Tamil + English benchmark samples (printed/degraded/mixed-language, plus the handwritten demo where the chosen model supports it).
4. Record actual CER/WER and field exact-match numbers; do not invent benchmark results.
5. Decide whether page-derivative/image endpoints are needed beyond the signed immutable-source viewer for the final demo contract.
