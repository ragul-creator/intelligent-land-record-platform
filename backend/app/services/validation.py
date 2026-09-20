"""H.2B.3 project validation checks for duplicate records and area mismatches."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.models import (
    Document,
    DocumentValidationResultRecord,
    Parcel,
    ParcelGeometryVersion,
    RecordParcelLink,
    ReviewTask,
    User,
)
from app.services.record_parcel_links import IDENTIFIER_FIELDS, document_evidence, normalized_identifier
from app.services.review import create_review_task


DEFAULT_AREA_RELATIVE_TOLERANCE = 0.05
VALIDATION_ISSUE_TARGET_TYPE = "VALIDATION_ISSUE"
VALIDATION_ISSUE_TYPES = frozenset({"DUPLICATE_RECORD", "AREA_MISMATCH"})
_SQFT_PER_M2 = 10.7639104167


@dataclass(frozen=True, slots=True)
class IdentifierEvidence:
    document_id: uuid.UUID
    validation_result_id: uuid.UUID
    field_id: uuid.UUID
    field_name: str
    value: str
    normalized_value: Any | None
    page_number: int
    source_id: str


@dataclass(frozen=True, slots=True)
class DuplicateRecordCandidate:
    normalized_identifier: str
    display_identifier: str
    document_ids: tuple[uuid.UUID, ...]
    validation_result_ids: tuple[uuid.UUID, ...]
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AreaComparisonInput:
    link_id: uuid.UUID
    document_id: uuid.UUID
    validation_result_id: uuid.UUID
    parcel_id: uuid.UUID
    parcel_identifier: str | None
    document_area_m2: float
    parcel_area_m2: float
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AreaMismatchCandidate:
    comparison: AreaComparisonInput
    relative_difference: float
    tolerance: float


@dataclass(frozen=True, slots=True)
class ValidationRunResult:
    created_count: int
    refreshed_count: int


def detect_duplicate_records(
    evidence: tuple[IdentifierEvidence, ...],
) -> tuple[DuplicateRecordCandidate, ...]:
    """Flag exact normalized identifiers present in two or more validated documents."""
    groups: dict[str, dict[uuid.UUID, IdentifierEvidence]] = {}
    for item in evidence:
        if item.field_name not in IDENTIFIER_FIELDS:
            continue
        candidate_text = item.normalized_value if isinstance(item.normalized_value, str) else item.value
        normalized = normalized_identifier(candidate_text)
        if not normalized:
            continue
        # Multiple extraction candidates inside one document do not make that record a duplicate.
        groups.setdefault(normalized, {}).setdefault(item.document_id, item)

    duplicates: list[DuplicateRecordCandidate] = []
    for normalized, by_document in groups.items():
        if len(by_document) < 2:
            continue
        rows = tuple(sorted(by_document.values(), key=lambda row: str(row.document_id)))
        duplicates.append(
            DuplicateRecordCandidate(
                normalized_identifier=normalized,
                display_identifier=rows[0].value,
                document_ids=tuple(row.document_id for row in rows),
                validation_result_ids=tuple(row.validation_result_id for row in rows),
                source_refs=tuple(
                    f"document:{row.document_id}:field:{row.field_id}:page:{row.page_number}"
                    for row in rows
                ),
            )
        )
    duplicates.sort(key=lambda item: (item.normalized_identifier, tuple(map(str, item.document_ids))))
    return tuple(duplicates)


def detect_area_mismatches(
    comparisons: tuple[AreaComparisonInput, ...],
    tolerance: float = DEFAULT_AREA_RELATIVE_TOLERANCE,
) -> tuple[AreaMismatchCandidate, ...]:
    """Compare authoritative record area evidence with the currently linked parcel area."""
    if tolerance < 0:
        raise ValueError("Area mismatch tolerance must be non-negative.")
    mismatches: list[AreaMismatchCandidate] = []
    for item in comparisons:
        if item.document_area_m2 <= 0 or item.parcel_area_m2 <= 0:
            continue
        relative_difference = abs(item.document_area_m2 - item.parcel_area_m2) / max(
            item.document_area_m2,
            item.parcel_area_m2,
        )
        if relative_difference > tolerance:
            mismatches.append(
                AreaMismatchCandidate(
                    comparison=item,
                    relative_difference=relative_difference,
                    tolerance=tolerance,
                )
            )
    mismatches.sort(key=lambda item: (-item.relative_difference, str(item.comparison.link_id)))
    return tuple(mismatches)


def _latest_validations(
    session: Session,
    project_id: uuid.UUID,
) -> tuple[DocumentValidationResultRecord, ...]:
    rows = session.scalars(
        select(DocumentValidationResultRecord)
        .join(Document, Document.id == DocumentValidationResultRecord.document_id)
        .where(
            Document.project_id == project_id,
            Document.status == "VALIDATED",
            DocumentValidationResultRecord.status == "VALID",
        )
        .order_by(
            DocumentValidationResultRecord.document_id,
            DocumentValidationResultRecord.version.desc(),
        )
    )
    latest: dict[uuid.UUID, DocumentValidationResultRecord] = {}
    for row in rows:
        latest.setdefault(row.document_id, row)
    return tuple(latest.values())


def _identifier_evidence(
    session: Session,
    validations: tuple[DocumentValidationResultRecord, ...],
) -> tuple[IdentifierEvidence, ...]:
    values: list[IdentifierEvidence] = []
    for validation in validations:
        for field in document_evidence(session, validation):
            if field.field_name not in IDENTIFIER_FIELDS:
                continue
            values.append(
                IdentifierEvidence(
                    document_id=validation.document_id,
                    validation_result_id=validation.id,
                    field_id=field.field_id,
                    field_name=field.field_name,
                    value=field.value,
                    normalized_value=field.normalized_value,
                    page_number=field.page_number,
                    source_id=field.source_id,
                )
            )
    return tuple(values)


def _document_area(
    session: Session,
    validation: DocumentValidationResultRecord,
) -> tuple[float | None, tuple[str, ...]]:
    refs: list[str] = []
    for field in document_evidence(session, validation):
        if field.field_name != "plot_area" or not isinstance(field.normalized_value, dict):
            continue
        value = field.normalized_value.get("value")
        unit = field.normalized_value.get("unit")
        if not isinstance(value, (int, float)) or value <= 0 or not isinstance(unit, str):
            continue
        if unit in {"sq_m", "sqm", "m2"}:
            area_m2 = float(value)
        elif unit in {"sq_ft", "sqft", "ft2"}:
            area_m2 = float(value) / _SQFT_PER_M2
        else:
            continue
        refs.append(
            f"document:{validation.document_id}:field:{field.field_id}:page:{field.page_number}"
        )
        return area_m2, tuple(refs)
    return None, ()


def _area_comparisons(
    session: Session,
    project_id: uuid.UUID,
) -> tuple[AreaComparisonInput, ...]:
    links = session.scalars(
        select(RecordParcelLink).where(
            RecordParcelLink.project_id == project_id,
            RecordParcelLink.link_status == "CONFIRMED",
        )
    )
    comparisons: list[AreaComparisonInput] = []
    for link in links:
        validation = session.get(DocumentValidationResultRecord, link.document_validation_result_id)
        parcel = session.get(Parcel, link.parcel_id)
        if validation is None or parcel is None:
            continue
        document_area_m2, document_refs = _document_area(session, validation)
        if document_area_m2 is None:
            continue
        version = session.scalar(
            select(ParcelGeometryVersion).where(
                ParcelGeometryVersion.parcel_id == parcel.id,
                ParcelGeometryVersion.version == parcel.current_geometry_version,
            )
        )
        if version is None:
            continue
        parcel_area_m2 = version.area_m2
        if parcel_area_m2 is None and version.area_sqft is not None:
            parcel_area_m2 = version.area_sqft / _SQFT_PER_M2
        if parcel_area_m2 is None:
            continue
        comparisons.append(
            AreaComparisonInput(
                link_id=link.id,
                document_id=link.document_id,
                validation_result_id=validation.id,
                parcel_id=parcel.id,
                parcel_identifier=parcel.external_identifier,
                document_area_m2=document_area_m2,
                parcel_area_m2=float(parcel_area_m2),
                source_refs=tuple(
                    sorted(
                        {
                            *document_refs,
                            f"record-parcel-link:{link.id}",
                            f"parcel:{parcel.id}:geometry-version:{parcel.current_geometry_version}",
                        }
                    )
                ),
            )
        )
    return tuple(comparisons)


def _existing_issue(
    session: Session,
    project_id: uuid.UUID,
    issue_key: str,
) -> ReviewTask | None:
    return session.scalar(
        select(ReviewTask)
        .where(
            ReviewTask.project_id == project_id,
            ReviewTask.target_type == VALIDATION_ISSUE_TARGET_TYPE,
            ReviewTask.metadata_json["validation_issue_key"].astext == issue_key,
        )
        .order_by(ReviewTask.created_at.desc())
    )


def _upsert_open_issue(
    session: Session,
    *,
    project_id: uuid.UUID,
    actor: User,
    issue_key: str,
    target_id: uuid.UUID,
    severity: str,
    summary: str,
    source_refs: tuple[str, ...],
    metadata: dict[str, Any],
) -> tuple[ReviewTask, bool, bool]:
    existing = _existing_issue(session, project_id, issue_key)
    if existing is not None:
        if existing.status == "OPEN":
            existing.severity = severity
            existing.summary = summary
            existing.source_refs_json = list(source_refs)
            existing.metadata_json = {
                **metadata,
                "validation_issue_key": issue_key,
            }
            return existing, False, True
        return existing, False, False

    task = create_review_task(
        session,
        project_id=project_id,
        queue_type="DOCUMENT",
        target_type=VALIDATION_ISSUE_TARGET_TYPE,
        target_id=target_id,
        severity=severity,
        summary=summary,
        source_refs=list(source_refs),
        metadata={
            **metadata,
            "validation_issue_key": issue_key,
        },
        blocking_issue_count=0,
        created_by_user_id=actor.id,
    )
    return task, True, False


def run_validation_checks(
    session: Session,
    *,
    project_id: uuid.UUID,
    actor: User,
    area_relative_tolerance: float = DEFAULT_AREA_RELATIVE_TOLERANCE,
) -> ValidationRunResult:
    """Run deterministic H.2B.3 checks without auto-deciding legal record validity."""
    created_count = 0
    refreshed_count = 0

    validations = _latest_validations(session, project_id)
    duplicates = detect_duplicate_records(_identifier_evidence(session, validations))
    for candidate in duplicates:
        issue_key = f"duplicate-record:{candidate.normalized_identifier}"
        _, created, refreshed = _upsert_open_issue(
            session,
            project_id=project_id,
            actor=actor,
            issue_key=issue_key,
            target_id=uuid.uuid5(project_id, issue_key),
            severity="HIGH",
            summary=(
                f"Potential duplicate record: {len(candidate.document_ids)} validated documents "
                f"share identifier {candidate.display_identifier}."
            ),
            source_refs=candidate.source_refs,
            metadata={
                "validation_issue_type": "DUPLICATE_RECORD",
                "normalized_identifier": candidate.normalized_identifier,
                "display_identifier": candidate.display_identifier,
                "document_ids": [str(value) for value in candidate.document_ids],
                "validation_result_ids": [str(value) for value in candidate.validation_result_ids],
                "document_count": len(candidate.document_ids),
                "interpretation": (
                    "Exact identifier reuse is a review flag only; it does not automatically prove "
                    "that the records are legally duplicate."
                ),
            },
        )
        created_count += int(created)
        refreshed_count += int(refreshed)

    mismatches = detect_area_mismatches(
        _area_comparisons(session, project_id),
        tolerance=area_relative_tolerance,
    )
    for candidate in mismatches:
        item = candidate.comparison
        issue_key = f"area-mismatch:{item.link_id}"
        severity = "HIGH" if candidate.relative_difference >= 0.20 else "MEDIUM"
        _, created, refreshed = _upsert_open_issue(
            session,
            project_id=project_id,
            actor=actor,
            issue_key=issue_key,
            target_id=item.link_id,
            severity=severity,
            summary=(
                "Area mismatch: validated document area and linked parcel area differ by "
                f"{candidate.relative_difference:.1%}."
            ),
            source_refs=item.source_refs,
            metadata={
                "validation_issue_type": "AREA_MISMATCH",
                "record_parcel_link_id": str(item.link_id),
                "document_id": str(item.document_id),
                "document_validation_result_id": str(item.validation_result_id),
                "parcel_id": str(item.parcel_id),
                "parcel_identifier": item.parcel_identifier,
                "document_area_m2": item.document_area_m2,
                "parcel_area_m2": item.parcel_area_m2,
                "relative_difference": candidate.relative_difference,
                "tolerance": candidate.tolerance,
                "interpretation": (
                    "The area difference requires human review; the check does not alter parcel "
                    "geometry or assert a legal boundary."
                ),
            },
        )
        created_count += int(created)
        refreshed_count += int(refreshed)

    record_audit(
        session,
        "validation.h2b3_checks_run",
        "project",
        project_id,
        actor_id=actor.id,
        project_id=project_id,
        metadata={
            "duplicate_record_candidates": len(duplicates),
            "area_mismatch_candidates": len(mismatches),
            "created_issue_count": created_count,
            "refreshed_issue_count": refreshed_count,
            "area_relative_tolerance": area_relative_tolerance,
        },
    )
    return ValidationRunResult(
        created_count=created_count,
        refreshed_count=refreshed_count,
    )
