# Phase H.2 — Tamil Nadu Demo Dataset And Presentation Guide

Phase H.2 adds a clearly synthetic Tamil Nadu/Chennai-area demonstration dataset plus a browser sign-in/project launcher. The dataset is designed to exercise the completed SIH18 → review → SIH12 → record↔parcel vertical slice without presenting synthetic values as government records.

## Seed the demo

Apply migrations first, then set a local demo password of at least 12 characters. The password is not committed, printed, or written to the audit log.

```powershell
$env:DEMO_SEED_PASSWORD = "replace-with-a-local-demo-password"

docker compose -f infrastructure\docker-compose.yml --env-file .env run --rm --build `
  -e DEMO_SEED_PASSWORD=$env:DEMO_SEED_PASSWORD `
  backend `
  python -m app.cli.seed_demo
```

The command prints the generated login IDs for ADMIN, OFFICER, REVIEWER, SURVEYOR, and VIEWER plus the demo project UUID. Open `http://localhost:5173/`, sign in with one of those login IDs and the password you supplied, and choose **Tamil Nadu Integrated Land Records Demo**.

The seed command is idempotent. Re-running it does not duplicate the project or the demo evidence. Existing demo-user passwords are not silently reset by a later run.

## What the dataset contains

- two synthetic Tamil + English document records: one `VALIDATED`, one `REVIEW_REQUIRED`
- persisted OCR text, confidence, extracted fields, validation output, source/page/bounding-box provenance
- an open human-review task for the lower-confidence record
- two synthetic draft cadastral parcels near Chennai coordinates, explicitly marked unverified and survey-required
- one AI-preliminary building footprint, one pathway, one residential land-use polygon, and one unresolved synthetic topology marker
- one imagery metadata record labelled synthetic and not survey evidence
- one confirmed Record ↔ Parcel workflow association for the validated record
- completed processing/GeoAI job records and audit events so the dashboard has meaningful persisted counts

All parcel identifiers begin with `TN-DEMO`, source references begin with `H2-SYNTHETIC-TN-DEMO`, and the project description states that the records are synthetic. These values must not be presented as official survey numbers, ownership proof, or government data.

## Suggested presentation flow

1. **Platform home:** sign in as OFFICER and open the Tamil Nadu demo project.
2. **Dashboard:** show persisted document, parcel, review, job, and confirmed-link counts. Explain that no synthetic progress percentage is fabricated.
3. **Documents:** open `tn_demo_validated_record.pdf`; show Tamil/English OCR text, structured fields, validation version, and the confirmed record↔parcel association.
4. **Web-GIS:** follow the parcel deep link. Show the parcel source/provenance, area in m² and sq ft, building footprint separation, pathway/land-use layers, and reverse link to the source record.
5. **Review workspace:** open the lower-confidence demo record case and show the canonical actions: Approve, Correct, Reject, Reprocess, Comment, Escalate.
6. **Role switch (optional):** sign in as VIEWER to demonstrate read-only access, or REVIEWER to demonstrate human-verification authority.

## Demo boundaries

The seed is an application demonstration, not a benchmark dataset. OCR confidence values and GIS geometries are synthetic fixtures used to exercise workflow code. They must not be used to claim statewide OCR accuracy, cadastral accuracy, legal boundary determination, or live LRMS/DILRMP integration.
