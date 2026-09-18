"""Deterministic, explainable record-to-parcel matching and link persistence."""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.models import (
    Document,
    DocumentExtractedField,
    DocumentValidationResultRecord,
    Parcel,
    ParcelGeometryVersion,
    RecordParcelLink,
    ReviewTask,
    User,
)
from app.services.review import apply_review_action, create_review_task


ACTIVE_LINK_STATUSES = ("SUGGESTED", "REVIEW_REQUIRED", "CONFIRMED")
IDENTIFIER_FIELDS = frozenset({"survey_number", "cadastral_identifier", "parcel_identifier", "khasra_number", "khata_number"})
ADMIN_FIELDS = ("village", "tehsil", "district")
_ADMIN_ALIASES = {"tehsil": ("tehsil", "taluk", "tahsil")}
_SQFT_PER_M2 = 10.7639104167


class RecordParcelLinkError(ValueError):
    """Raised when a requested record-to-parcel action is not safe or valid."""


@dataclass(frozen=True, slots=True)
class LinkMatchingPolicy:
    """Conservative thresholds kept together for explainable future configuration."""

    auto_confirm_threshold: float = 0.95
    area_relative_tolerance: float = 0.05
    exact_identifier_score: float = 0.90
    admin_match_increment: float = 0.03
    area_match_increment: float = 0.03
    conflicting_admin_penalty: float = 0.45
    policy_version: str = "record-parcel-link-mvp-v1"


@dataclass(frozen=True, slots=True)
class PersistedFieldEvidence:
    field_id: uuid.UUID
    field_name: str
    value: str
    normalized_value: Any | None
    page_number: int
    bounding_box: dict[str, int] | None
    source_id: str


@dataclass(frozen=True, slots=True)
class ParcelMatchInput:
    parcel_id: uuid.UUID
    external_identifier: str | None
    source: str
    source_reference: str | None
    metadata: dict[str, Any]
    area_m2: float | None
    area_sqft: float | None


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    parcel_id: uuid.UUID
    parcel_display_identifier: str | None
    status: str
    method: str
    confidence: float
    rationale: dict[str, Any]
    provenance: dict[str, Any]
    review_required: bool


def normalized_text(value: str) -> str:
    """Normalize for comparisons only; original OCR evidence remains untouched."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def normalized_identifier(value: str) -> str:
    return re.sub(r"\s*([/-])\s*", r"\1", normalized_text(value))


def _field_text(evidence: PersistedFieldEvidence) -> str:
    if isinstance(evidence.normalized_value, str) and evidence.normalized_value.strip():
        return evidence.normalized_value
    return evidence.value


def _field_values(evidence: tuple[PersistedFieldEvidence, ...], name: str) -> tuple[PersistedFieldEvidence, ...]:
    aliases = _ADMIN_ALIASES.get(name, (name,))
    return tuple(item for item in evidence if item.field_name in aliases)


def _document_area_m2(evidence: tuple[PersistedFieldEvidence, ...]) -> float | None:
    for item in evidence:
        if item.field_name != "plot_area" or not isinstance(item.normalized_value, dict):
            continue
        value, unit = item.normalized_value.get("value"), item.normalized_value.get("unit")
        if not isinstance(value, (int, float)) or not isinstance(unit, str) or value <= 0:
            continue
        if unit in {"sq_m", "sqm", "m2"}:
            return float(value)
        if unit in {"sq_ft", "sqft", "ft2"}:
            return float(value) / _SQFT_PER_M2
    return None


def _parcel_admin_value(metadata: dict[str, Any], field_name: str) -> str | None:
    for key in _ADMIN_ALIASES.get(field_name, (field_name,)):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _evidence_reference(item: PersistedFieldEvidence) -> dict[str, Any]:
    return {
        "field_id": str(item.field_id),
        "field_name": item.field_name,
        "value": item.value,
        "source_id": item.source_id,
        "page_number": item.page_number,
        "bounding_box": item.bounding_box,
    }


def match_record_to_parcels(
    evidence: tuple[PersistedFieldEvidence, ...],
    parcels: tuple[ParcelMatchInput, ...],
    policy: LinkMatchingPolicy = LinkMatchingPolicy(),
) -> tuple[MatchCandidate, ...]:
    """Return deterministic candidates without inferring identifiers or ownership."""
    identifier_evidence = tuple(item for item in evidence if item.field_name in IDENTIFIER_FIELDS and _field_text(item).strip())
    document_area_m2 = _document_area_m2(evidence)
    candidates: list[MatchCandidate] = []

    for parcel in parcels:
        parcel_identifier = normalized_identifier(parcel.external_identifier) if parcel.external_identifier else None
        matched_identifiers = tuple(item for item in identifier_evidence if parcel_identifier and normalized_identifier(_field_text(item)) == parcel_identifier)
        admin_matches: list[str] = []
        admin_conflicts: list[str] = []
        contributing = list(matched_identifiers)
        for field_name in ADMIN_FIELDS:
            parcel_value = _parcel_admin_value(parcel.metadata, field_name)
            values = _field_values(evidence, field_name)
            if parcel_value is None or not values:
                continue
            normalized_parcel_value = normalized_text(parcel_value)
            matching_values = [item for item in values if normalized_text(_field_text(item)) == normalized_parcel_value]
            if matching_values:
                admin_matches.append(field_name)
                contributing.extend(matching_values)
            else:
                admin_conflicts.append(field_name)

        parcel_area_m2 = parcel.area_m2 or (parcel.area_sqft / _SQFT_PER_M2 if parcel.area_sqft else None)
        area_comparison: dict[str, Any] | None = None
        area_compatible = False
        if document_area_m2 is not None and parcel_area_m2 is not None and parcel_area_m2 > 0:
            relative_difference = abs(document_area_m2 - parcel_area_m2) / max(document_area_m2, parcel_area_m2)
            area_compatible = relative_difference <= policy.area_relative_tolerance
            area_comparison = {
                "document_area_m2": document_area_m2,
                "parcel_area_m2": parcel_area_m2,
                "relative_difference": relative_difference,
                "tolerance": policy.area_relative_tolerance,
                "compatible": area_compatible,
            }

        if not matched_identifiers:
            continue
        method, score = "EXACT_SURVEY_IDENTIFIER", policy.exact_identifier_score
        score += min(len(admin_matches), 2) * policy.admin_match_increment
        if area_compatible:
            score += policy.area_match_increment
        score = max(0.0, min(1.0, score - len(admin_conflicts) * policy.conflicting_admin_penalty))
        rationale = {
            "policy_version": policy.policy_version,
            "match_factors": {
                "exact_identifier": bool(matched_identifiers),
                "matching_admin_fields": admin_matches,
                "conflicting_admin_fields": admin_conflicts,
                "area_compatible": area_compatible,
            },
            "area_comparison": area_comparison,
        }
        unique_contributing = {item.field_id: item for item in contributing}
        provenance = {
            "document_fields": [_evidence_reference(item) for item in sorted(unique_contributing.values(), key=lambda row: str(row.field_id))],
            "parcel": {
                "external_identifier": parcel.external_identifier,
                "source": parcel.source,
                "source_reference": parcel.source_reference,
                "metadata_used": {name: _parcel_admin_value(parcel.metadata, name) for name in ADMIN_FIELDS if _parcel_admin_value(parcel.metadata, name) is not None},
            },
        }
        candidates.append(MatchCandidate(parcel.parcel_id, parcel.external_identifier, "SUGGESTED", method, score, rationale, provenance, True))

    candidates.sort(key=lambda item: (-item.confidence, str(item.parcel_id)))
    if len(candidates) == 1:
        only = candidates[0]
        factors = only.rationale["match_factors"]
        can_confirm = (
            only.method == "EXACT_SURVEY_IDENTIFIER"
            and bool(factors["matching_admin_fields"])
            and not factors["conflicting_admin_fields"]
            and only.confidence >= policy.auto_confirm_threshold
        )
        status = "CONFIRMED" if can_confirm else "REVIEW_REQUIRED"
        return (MatchCandidate(only.parcel_id, only.parcel_display_identifier, status, only.method, only.confidence, only.rationale, only.provenance, status == "REVIEW_REQUIRED"),)
    if candidates:
        return tuple(MatchCandidate(item.parcel_id, item.parcel_display_identifier, "REVIEW_REQUIRED", item.method, item.confidence, item.rationale, item.provenance, True) for item in candidates)
    return ()


def _source_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    values: dict[str, Any] = {}
    for key in ("properties", "metadata"):
        section = payload.get(key)
        if isinstance(section, dict):
            values.update({str(name): value for name, value in section.items() if isinstance(value, (str, int, float, bool))})
    feature = payload.get("feature")
    if isinstance(feature, dict) and isinstance(feature.get("properties"), dict):
        values.update({str(name): value for name, value in feature["properties"].items() if isinstance(value, (str, int, float, bool))})
    return values


def document_evidence(session: Session, validation: DocumentValidationResultRecord) -> tuple[PersistedFieldEvidence, ...]:
    records = session.scalars(
        select(DocumentExtractedField)
        .where(
            DocumentExtractedField.document_id == validation.document_id,
            DocumentExtractedField.ocr_result_id == validation.ocr_result_id,
        )
        .order_by(DocumentExtractedField.candidate_index)
    )
    return tuple(
        PersistedFieldEvidence(
            field_id=record.id,
            field_name=record.field_name,
            value=record.original_value,
            normalized_value=record.normalized_value_json,
            page_number=record.page_number,
            bounding_box=record.bounding_box_json,
            source_id=record.source_id,
        )
        for record in records
    )


def parcel_inputs(session: Session, project_id: uuid.UUID) -> tuple[ParcelMatchInput, ...]:
    parcels = session.scalars(select(Parcel).where(Parcel.project_id == project_id).order_by(Parcel.id))
    inputs: list[ParcelMatchInput] = []
    for parcel in parcels:
        version = session.scalar(
            select(ParcelGeometryVersion).where(
                ParcelGeometryVersion.parcel_id == parcel.id,
                ParcelGeometryVersion.version == parcel.current_geometry_version,
            )
        )
        inputs.append(
            ParcelMatchInput(
                parcel_id=parcel.id,
                external_identifier=parcel.external_identifier,
                source=parcel.source,
                source_reference=parcel.source_reference,
                metadata=_source_metadata(version.source_geometry_json if version else None),
                area_m2=version.area_m2 if version else None,
                area_sqft=version.area_sqft if version else None,
            )
        )
    return tuple(inputs)


def validated_context(session: Session, project_id: uuid.UUID, document_id: uuid.UUID, validation_id: uuid.UUID) -> tuple[Document, DocumentValidationResultRecord]:
    document = session.get(Document, document_id)
    validation = session.get(DocumentValidationResultRecord, validation_id)
    if document is None or validation is None or document.project_id != project_id or validation.document_id != document.id:
        raise RecordParcelLinkError("Validated document record was not found in this project.")
    if document.status != "VALIDATED" or validation.status != "VALID":
        raise RecordParcelLinkError("Record-to-parcel links require a validated document result.")
    return document, validation


def _active_link(session: Session, validation_id: uuid.UUID, parcel_id: uuid.UUID) -> RecordParcelLink | None:
    return session.scalar(
        select(RecordParcelLink)
        .where(
            RecordParcelLink.document_validation_result_id == validation_id,
            RecordParcelLink.parcel_id == parcel_id,
            RecordParcelLink.link_status.in_(ACTIVE_LINK_STATUSES),
        )
        .with_for_update()
    )


def _review_task_for_link(session: Session, link: RecordParcelLink) -> ReviewTask | None:
    if link.review_task_id:
        return session.get(ReviewTask, link.review_task_id)
    return session.scalar(
        select(ReviewTask).where(
            ReviewTask.project_id == link.project_id,
            ReviewTask.target_type == "RECORD_PARCEL_LINK",
            ReviewTask.target_id == link.id,
            ReviewTask.status == "OPEN",
        )
    )


def _create_link_review_task(session: Session, link: RecordParcelLink, actor: User) -> None:
    if _review_task_for_link(session, link) is not None:
        return
    source_refs = [f"parcel:{link.parcel_id}"]
    source_refs.extend(
        f"document:{item['source_id']}:page:{item['page_number']}"
        for item in link.provenance_json.get("document_fields", [])
    )
    task = create_review_task(
        session,
        project_id=link.project_id,
        queue_type="DOCUMENT",
        target_type="RECORD_PARCEL_LINK",
        target_id=link.id,
        severity="MEDIUM",
        summary="Record-to-parcel association requires human review.",
        source_refs=sorted(set(source_refs)),
        metadata={"record_parcel_link_id": str(link.id), "document_validation_result_id": str(link.document_validation_result_id), "parcel_id": str(link.parcel_id), "confidence": link.confidence, "method": link.link_method},
        created_by_user_id=actor.id,
    )
    link.review_task_id = task.id


def generate_suggestions(session: Session, *, project_id: uuid.UUID, document_id: uuid.UUID, validation_id: uuid.UUID, actor: User, policy: LinkMatchingPolicy = LinkMatchingPolicy()) -> tuple[RecordParcelLink, ...]:
    document, validation = validated_context(session, project_id, document_id, validation_id)
    generated: list[RecordParcelLink] = []
    for candidate in match_record_to_parcels(document_evidence(session, validation), parcel_inputs(session, project_id), policy):
        existing = _active_link(session, validation.id, candidate.parcel_id)
        if existing is not None:
            generated.append(existing)
            continue
        link = RecordParcelLink(
            project_id=project_id,
            document_id=document.id,
            document_validation_result_id=validation.id,
            parcel_id=candidate.parcel_id,
            link_status=candidate.status,
            link_method=candidate.method,
            confidence=candidate.confidence,
            rationale_json=candidate.rationale,
            provenance_json={**candidate.provenance, "document_validation_result_id": str(validation.id)},
            created_by_user_id=actor.id,
        )
        session.add(link)
        session.flush()
        if candidate.review_required:
            _create_link_review_task(session, link, actor)
        event = "record_parcel_link.auto_confirmed" if link.link_status == "CONFIRMED" else "record_parcel_link.suggested"
        record_audit(session, event, "record_parcel_link", link.id, actor_id=actor.id, project_id=project_id, metadata={"document_id": str(document.id), "validation_result_id": str(validation.id), "parcel_id": str(link.parcel_id), "confidence": link.confidence, "method": link.link_method, "status": link.link_status})
        generated.append(link)
    return tuple(generated)


def manual_link(session: Session, *, project_id: uuid.UUID, document_id: uuid.UUID, validation_id: uuid.UUID, parcel_id: uuid.UUID, rationale: str, actor: User) -> RecordParcelLink:
    document, validation = validated_context(session, project_id, document_id, validation_id)
    parcel = session.get(Parcel, parcel_id)
    if parcel is None or parcel.project_id != project_id:
        raise RecordParcelLinkError("Parcel was not found in this project.")
    existing = _active_link(session, validation.id, parcel.id)
    if existing is not None:
        return existing
    confirmed = session.scalar(
        select(RecordParcelLink).where(
            RecordParcelLink.document_validation_result_id == validation.id,
            RecordParcelLink.link_status == "CONFIRMED",
        ).with_for_update()
    )
    if confirmed is not None:
        raise RecordParcelLinkError("This validated record already has a confirmed parcel association.")
    link = RecordParcelLink(
        project_id=project_id,
        document_id=document.id,
        document_validation_result_id=validation.id,
        parcel_id=parcel.id,
        link_status="CONFIRMED",
        link_method="MANUAL",
        confidence=None,
        rationale_json={"policy_version": "record-parcel-link-mvp-v1", "match_factors": {"manual": True}, "manual_rationale": rationale},
        provenance_json={"document_validation_result_id": str(validation.id), "document_fields": [], "parcel": {"external_identifier": parcel.external_identifier, "source": parcel.source, "source_reference": parcel.source_reference}},
        created_by_user_id=actor.id,
        reviewed_by_user_id=actor.id,
        reviewed_at=datetime.now(UTC),
        review_reason=rationale,
    )
    session.add(link)
    session.flush()
    record_audit(session, "record_parcel_link.manually_confirmed", "record_parcel_link", link.id, actor_id=actor.id, project_id=project_id, metadata={"document_id": str(document.id), "validation_result_id": str(validation.id), "parcel_id": str(parcel.id), "method": "MANUAL"})
    return link


def resolve_link(session: Session, *, link: RecordParcelLink, actor: User, action: str, reason: str | None = None) -> RecordParcelLink:
    if link.link_status not in {"SUGGESTED", "REVIEW_REQUIRED"}:
        raise RecordParcelLinkError("Only pending record-to-parcel links can be resolved.")
    action = action.upper()
    if action not in {"CONFIRM", "REJECT"}:
        raise RecordParcelLinkError("Link action must be CONFIRM or REJECT.")
    if action == "CONFIRM":
        confirmed = session.scalar(
            select(RecordParcelLink).where(
                RecordParcelLink.document_validation_result_id == link.document_validation_result_id,
                RecordParcelLink.link_status == "CONFIRMED",
                RecordParcelLink.id != link.id,
            ).with_for_update()
        )
        if confirmed is not None:
            raise RecordParcelLinkError("This validated record already has a confirmed parcel association.")
        link.link_status = "CONFIRMED"
    else:
        if not reason or not reason.strip():
            raise RecordParcelLinkError("Rejecting a link requires a reason.")
        link.link_status = "REJECTED"
    link.reviewed_by_user_id = actor.id
    link.reviewed_at = datetime.now(UTC)
    link.review_reason = reason.strip() if reason else None
    task = _review_task_for_link(session, link)
    if task is not None and task.status == "OPEN":
        apply_review_action(session, task, actor=actor, action="APPROVE" if action == "CONFIRM" else "REJECT", reason=reason)
    record_audit(session, "record_parcel_link.manually_confirmed" if action == "CONFIRM" else "record_parcel_link.rejected", "record_parcel_link", link.id, actor_id=actor.id, project_id=link.project_id, metadata={"document_id": str(link.document_id), "validation_result_id": str(link.document_validation_result_id), "parcel_id": str(link.parcel_id), "confidence": link.confidence, "method": link.link_method, "reason": link.review_reason})
    return link
