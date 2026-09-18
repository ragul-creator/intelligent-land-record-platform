"""Project-scoped Phase G.1 APIs for validated record-to-parcel associations."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db_session
from app.core.errors import ApiError, not_found
from app.models import Document, Parcel, RecordParcelLink, User
from app.schemas.common import PageMetadata
from app.schemas.record_links import (
    LinkResolutionRequest,
    LinkSuggestionRequest,
    ManualLinkRequest,
    RecordParcelLinkListResponse,
    RecordParcelLinkResponse,
    RecordParcelLinkSuggestionResponse,
)
from app.services.project_access import get_project_for_user
from app.services.record_parcel_links import (
    RecordParcelLinkError,
    generate_suggestions,
    manual_link,
    resolve_link,
)


router = APIRouter(prefix="/projects/{project_id}", tags=["record-parcel-links"])


def _document(session: Session, project_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.project_id != project_id:
        raise not_found("DOCUMENT_NOT_FOUND", "The requested document was not found.")
    return document


def _link(session: Session, project_id: uuid.UUID, link_id: uuid.UUID) -> RecordParcelLink:
    link = session.get(RecordParcelLink, link_id)
    if link is None or link.project_id != project_id:
        raise not_found("RECORD_PARCEL_LINK_NOT_FOUND", "The requested record-to-parcel link was not found.")
    return link


def _response(session: Session, link: RecordParcelLink) -> RecordParcelLinkResponse:
    parcel = session.get(Parcel, link.parcel_id)
    return RecordParcelLinkResponse(
        id=link.id,
        project_id=link.project_id,
        document_id=link.document_id,
        document_validation_result_id=link.document_validation_result_id,
        parcel_id=link.parcel_id,
        parcel_display_identifier=parcel.external_identifier if parcel else None,
        link_status=link.link_status,
        link_method=link.link_method,
        confidence=link.confidence,
        rationale=dict(link.rationale_json or {}),
        provenance=dict(link.provenance_json or {}),
        review_required=link.link_status == "REVIEW_REQUIRED",
        review_task_id=link.review_task_id,
        created_by_user_id=link.created_by_user_id,
        reviewed_by_user_id=link.reviewed_by_user_id,
        reviewed_at=link.reviewed_at,
        review_reason=link.review_reason,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


@router.get("/documents/{document_id}/record-parcel-links", response_model=RecordParcelLinkListResponse)
def list_document_links(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    validation_result_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> RecordParcelLinkListResponse:
    get_project_for_user(session, user, project_id, "record:read")
    _document(session, project_id, document_id)
    filters = [RecordParcelLink.project_id == project_id, RecordParcelLink.document_id == document_id]
    if validation_result_id is not None:
        filters.append(RecordParcelLink.document_validation_result_id == validation_result_id)
    total = session.scalar(select(func.count(RecordParcelLink.id)).where(*filters)) or 0
    links = session.scalars(
        select(RecordParcelLink)
        .where(*filters)
        .order_by(RecordParcelLink.created_at.desc(), RecordParcelLink.id)
        .limit(limit)
        .offset(offset)
    )
    return RecordParcelLinkListResponse(
        items=[_response(session, link) for link in links],
        page=PageMetadata(limit=limit, offset=offset, total=total),
    )


@router.post(
    "/documents/{document_id}/record-parcel-links/suggestions",
    response_model=RecordParcelLinkSuggestionResponse,
    status_code=status.HTTP_201_CREATED,
)
def suggest_document_links(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    request: LinkSuggestionRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> RecordParcelLinkSuggestionResponse:
    get_project_for_user(session, user, project_id, "validation:run")
    try:
        links = generate_suggestions(
            session,
            project_id=project_id,
            document_id=document_id,
            validation_id=request.document_validation_result_id,
            actor=user,
        )
    except RecordParcelLinkError as error:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "RECORD_PARCEL_LINK_INVALID", str(error)) from error
    session.commit()
    return RecordParcelLinkSuggestionResponse(items=[_response(session, link) for link in links])


@router.post(
    "/documents/{document_id}/record-parcel-links/manual",
    response_model=RecordParcelLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_document_link(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    request: ManualLinkRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> RecordParcelLinkResponse:
    get_project_for_user(session, user, project_id, "validation:resolve")
    try:
        link = manual_link(
            session,
            project_id=project_id,
            document_id=document_id,
            validation_id=request.document_validation_result_id,
            parcel_id=request.parcel_id,
            rationale=request.rationale.strip(),
            actor=user,
        )
    except RecordParcelLinkError as error:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "RECORD_PARCEL_LINK_INVALID", str(error)) from error
    session.commit()
    return _response(session, link)


@router.post("/record-parcel-links/{link_id}/confirm", response_model=RecordParcelLinkResponse)
def confirm_link(
    project_id: uuid.UUID,
    link_id: uuid.UUID,
    request: LinkResolutionRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> RecordParcelLinkResponse:
    get_project_for_user(session, user, project_id, "validation:resolve")
    try:
        link = resolve_link(session, link=_link(session, project_id, link_id), actor=user, action="CONFIRM", reason=request.reason)
    except RecordParcelLinkError as error:
        raise ApiError(status.HTTP_409_CONFLICT, "RECORD_PARCEL_LINK_INVALID", str(error)) from error
    session.commit()
    return _response(session, link)


@router.post("/record-parcel-links/{link_id}/reject", response_model=RecordParcelLinkResponse)
def reject_link(
    project_id: uuid.UUID,
    link_id: uuid.UUID,
    request: LinkResolutionRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> RecordParcelLinkResponse:
    get_project_for_user(session, user, project_id, "validation:resolve")
    try:
        link = resolve_link(session, link=_link(session, project_id, link_id), actor=user, action="REJECT", reason=request.reason)
    except RecordParcelLinkError as error:
        raise ApiError(status.HTTP_409_CONFLICT, "RECORD_PARCEL_LINK_INVALID", str(error)) from error
    session.commit()
    return _response(session, link)


@router.get("/parcels/{parcel_id}/record-parcel-links", response_model=RecordParcelLinkListResponse)
def list_parcel_links(
    project_id: uuid.UUID,
    parcel_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> RecordParcelLinkListResponse:
    get_project_for_user(session, user, project_id, "record:read")
    parcel = session.get(Parcel, parcel_id)
    if parcel is None or parcel.project_id != project_id:
        raise not_found("PARCEL_NOT_FOUND", "The requested parcel was not found.")
    filters = [RecordParcelLink.project_id == project_id, RecordParcelLink.parcel_id == parcel_id]
    total = session.scalar(select(func.count(RecordParcelLink.id)).where(*filters)) or 0
    links = session.scalars(
        select(RecordParcelLink)
        .where(*filters)
        .order_by(RecordParcelLink.created_at.desc(), RecordParcelLink.id)
        .limit(limit)
        .offset(offset)
    )
    return RecordParcelLinkListResponse(
        items=[_response(session, link) for link in links],
        page=PageMetadata(limit=limit, offset=offset, total=total),
    )
