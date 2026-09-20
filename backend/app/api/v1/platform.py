"""H.2B.4 project search and evidence-preserving export endpoints."""

from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import get_current_user, user_permissions
from app.core.database import get_db_session
from app.models import (
    Document,
    DocumentExtractedField,
    DocumentFieldCorrection,
    DocumentOcrResultRecord,
    DocumentValidationResultRecord,
    File,
    Parcel,
    User,
)
from app.schemas.platform import (
    ExportDescriptor,
    ExportManifestResponse,
    ProjectSearchItem,
    ProjectSearchResponse,
)
from app.services.geoai import current_version, geometry_geojson
from app.services.project_access import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}", tags=["platform hardening"])


def _latest_ocr(session: Session, document_id: uuid.UUID) -> DocumentOcrResultRecord | None:
    return session.scalar(
        select(DocumentOcrResultRecord)
        .where(DocumentOcrResultRecord.document_id == document_id)
        .order_by(DocumentOcrResultRecord.version.desc())
    )


def _latest_validation(
    session: Session, document_id: uuid.UUID
) -> DocumentValidationResultRecord | None:
    return session.scalar(
        select(DocumentValidationResultRecord)
        .where(DocumentValidationResultRecord.document_id == document_id)
        .order_by(DocumentValidationResultRecord.version.desc())
    )


def _effective_field_value(session: Session, field: DocumentExtractedField) -> str:
    correction = session.scalar(
        select(DocumentFieldCorrection)
        .where(DocumentFieldCorrection.extracted_field_id == field.id)
        .order_by(DocumentFieldCorrection.version.desc())
    )
    return correction.corrected_value if correction is not None else field.original_value


@router.get("/search", response_model=ProjectSearchResponse)
def search_project(
    project_id: uuid.UUID,
    q: str = Query(min_length=2, max_length=255),
    limit: int = Query(default=30, ge=1, le=100),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectSearchResponse:
    project = get_project_for_user(session, user, project_id, "project:read")
    permissions = user_permissions(session, user.id)
    query = q.strip()
    pattern = f"%{query}%"
    items: list[ProjectSearchItem] = []

    document_rows = session.execute(
        select(Document, File)
        .join(File, File.id == Document.file_id)
        .where(Document.project_id == project.id, File.original_name.ilike(pattern))
        .order_by(Document.updated_at.desc(), Document.id)
        .limit(limit)
    ).all()
    for document, source in document_rows:
        items.append(
            ProjectSearchItem(
                kind="DOCUMENT",
                id=document.id,
                title=source.original_name,
                subtitle="Document source",
                status=document.status,
                matched_value=source.original_name,
                preliminary=document.status != "VALIDATED",
            )
        )

    if len(items) < limit:
        parcel_rows = list(
            session.scalars(
                select(Parcel)
                .where(
                    Parcel.project_id == project.id,
                    Parcel.external_identifier.is_not(None),
                    Parcel.external_identifier.ilike(pattern),
                )
                .order_by(Parcel.updated_at.desc(), Parcel.id)
                .limit(limit - len(items))
            )
        )
        for parcel in parcel_rows:
            items.append(
                ProjectSearchItem(
                    kind="PARCEL",
                    id=parcel.id,
                    title=parcel.external_identifier or str(parcel.id),
                    subtitle="Parcel identifier",
                    status=parcel.status,
                    matched_value=parcel.external_identifier or "",
                    preliminary=parcel.verification_status != "VERIFIED",
                )
            )

    if len(items) < limit and "field:read" in permissions:
        field_rows = session.execute(
            select(DocumentExtractedField, Document, File)
            .join(Document, Document.id == DocumentExtractedField.document_id)
            .join(File, File.id == Document.file_id)
            .where(
                Document.project_id == project.id,
                DocumentExtractedField.original_value.ilike(pattern),
            )
            .order_by(Document.updated_at.desc(), DocumentExtractedField.candidate_index)
            .limit(limit - len(items))
        ).all()
        seen: set[tuple[uuid.UUID, str]] = set()
        for field, document, source in field_rows:
            key = (document.id, field.field_name)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                ProjectSearchItem(
                    kind="FIELD",
                    id=document.id,
                    evidence_id=field.id,
                    title=f"{field.field_name.replace('_', ' ').title()}: {field.original_value}",
                    subtitle=source.original_name,
                    status=document.status,
                    matched_value=field.original_value,
                    preliminary=document.status != "VALIDATED",
                )
            )
            if len(items) >= limit:
                break

    if len(items) < limit and "field:read" in permissions:
        correction_rows = session.execute(
            select(DocumentFieldCorrection, DocumentExtractedField, Document, File)
            .join(
                DocumentExtractedField,
                DocumentExtractedField.id == DocumentFieldCorrection.extracted_field_id,
            )
            .join(Document, Document.id == DocumentFieldCorrection.document_id)
            .join(File, File.id == Document.file_id)
            .where(
                Document.project_id == project.id,
                DocumentFieldCorrection.corrected_value.ilike(pattern),
            )
            .order_by(Document.updated_at.desc(), DocumentFieldCorrection.version.desc())
            .limit(limit - len(items))
        ).all()
        existing_evidence = {item.evidence_id for item in items if item.evidence_id is not None}
        for correction, field, document, source in correction_rows:
            if field.id in existing_evidence:
                continue
            items.append(
                ProjectSearchItem(
                    kind="FIELD",
                    id=document.id,
                    evidence_id=field.id,
                    title=f"{field.field_name.replace('_', ' ').title()}: {correction.corrected_value}",
                    subtitle=f"{source.original_name} · human correction v{correction.version}",
                    status=document.status,
                    matched_value=correction.corrected_value,
                    preliminary=document.status != "VALIDATED",
                )
            )
            existing_evidence.add(field.id)
            if len(items) >= limit:
                break

    return ProjectSearchResponse(query=query, items=items, total=len(items))


@router.get("/exports", response_model=ExportManifestResponse)
def export_manifest(
    project_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ExportManifestResponse:
    project = get_project_for_user(session, user, project_id, "export:read")
    return ExportManifestResponse(
        project_id=project.id,
        items=[
            ExportDescriptor(
                code="RECORDS_CSV",
                label="Land-record evidence CSV",
                path=f"/api/v1/projects/{project.id}/exports/records.csv",
                media_type="text/csv",
                description="Current document workflow status plus selected extracted/corrected record fields.",
            ),
            ExportDescriptor(
                code="PARCELS_GEOJSON",
                label="Parcel GeoJSON",
                path=f"/api/v1/projects/{project.id}/exports/parcels.geojson",
                media_type="application/geo+json",
                description="Persisted parcel geometries with draft and verification labels preserved.",
            ),
        ],
    )


@router.get("/exports/records.csv")
def export_records_csv(
    project_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> Response:
    project = get_project_for_user(session, user, project_id, "export:read")
    permissions = user_permissions(session, user.id)
    output = io.StringIO(newline="")
    columns = [
        "document_id",
        "filename",
        "workflow_status",
        "validation_status",
        "survey_number",
        "parcel_identifier",
        "plot_area",
        "village",
        "district",
        "evidence_note",
    ]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    documents = list(
        session.scalars(
            select(Document)
            .where(Document.project_id == project.id)
            .order_by(Document.created_at, Document.id)
        )
    )
    for document in documents:
        source = session.get(File, document.file_id)
        ocr = _latest_ocr(session, document.id)
        validation = _latest_validation(session, document.id)
        values: dict[str, str] = {}
        can_export_field_values = "field:read" in permissions or (
            document.status == "VALIDATED" and "record:read" in permissions
        )
        if ocr is not None and can_export_field_values:
            fields = list(
                session.scalars(
                    select(DocumentExtractedField)
                    .where(
                        DocumentExtractedField.document_id == document.id,
                        DocumentExtractedField.ocr_result_id == ocr.id,
                    )
                    .order_by(DocumentExtractedField.candidate_index)
                )
            )
            for field in fields:
                values.setdefault(field.field_name, _effective_field_value(session, field))
        writer.writerow(
            {
                "document_id": str(document.id),
                "filename": source.original_name if source is not None else "",
                "workflow_status": document.status,
                "validation_status": validation.status if validation is not None else "",
                "survey_number": values.get("survey_number", ""),
                "parcel_identifier": values.get("parcel_identifier", values.get("cadastral_identifier", "")),
                "plot_area": values.get("plot_area", ""),
                "village": values.get("village", ""),
                "district": values.get("district", ""),
                "evidence_note": (
                    "Workflow evidence only; AI-extracted or human-corrected values are not statutory ownership proof."
                ),
            }
        )
    record_audit(
        session,
        "project.export_records_csv",
        "project",
        project.id,
        actor_id=user.id,
        project_id=project.id,
        metadata={"document_count": len(documents)},
    )
    session.commit()
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="project-{project.id}-records.csv"'},
    )


@router.get("/exports/parcels.geojson")
def export_parcels_geojson(
    project_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    project = get_project_for_user(session, user, project_id, "export:read")
    parcels = list(
        session.scalars(
            select(Parcel)
            .where(Parcel.project_id == project.id)
            .order_by(Parcel.created_at, Parcel.id)
        )
    )
    features = []
    for parcel in parcels:
        version = current_version(session, parcel)
        geometry = geometry_geojson(version.geometry)
        if geometry is None:
            continue
        features.append(
            {
                "type": "Feature",
                "id": str(parcel.id),
                "geometry": geometry,
                "properties": {
                    "external_identifier": parcel.external_identifier,
                    "status": parcel.status,
                    "verification_status": parcel.verification_status,
                    "source": parcel.source,
                    "source_reference": parcel.source_reference,
                    "current_geometry_version": parcel.current_geometry_version,
                    "area_m2": version.area_m2,
                    "area_sqft": version.area_sqft,
                    "preliminary": parcel.verification_status != "VERIFIED",
                    "legal_boundary_asserted": False,
                },
            }
        )
    record_audit(
        session,
        "project.export_parcels_geojson",
        "project",
        project.id,
        actor_id=user.id,
        project_id=project.id,
        metadata={"feature_count": len(features)},
    )
    session.commit()
    return JSONResponse(
        content={
            "type": "FeatureCollection",
            "project_id": str(project.id),
            "disclaimer": (
                "Persisted parcel geometry is exported with its verification state. "
                "Draft or unverified geometry is preliminary and is not statutory boundary certification."
            ),
            "features": features,
        },
        media_type="application/geo+json",
        headers={"Content-Disposition": f'attachment; filename="project-{project.id}-parcels.geojson"'},
    )
