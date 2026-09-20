"""Phase G.2 project-scoped operational dashboard API."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db_session
from app.core.errors import not_found
from app.models import (
    Building, Document, GeoAIJob, ImageryAsset, LandUseFeature, Parcel,
    ProcessingJob, ProjectMember, RecordParcelLink, ReviewTask, Road, User,
)
from app.schemas.dashboard import (
    DashboardAttentionMetrics, DashboardDocumentMetrics, DashboardGeoMetrics,
    DashboardJobMetrics, DashboardLinkMetrics, DashboardReviewMetrics,
    DashboardVisibilityPolicy, ProjectDashboardResponse,
)
from app.schemas.projects import ProjectResponse, StatusCount
from app.services.project_access import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}", tags=["dashboard"])


def _project_response(project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description, state=project.state,
        owner_id=project.owner_id, created_at=project.created_at, updated_at=project.updated_at,
    )


@router.get("/dashboard", response_model=ProjectDashboardResponse)
def get_project_dashboard(
    project_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectDashboardResponse:
    project = get_project_for_user(session, user, project_id, "dashboard:read")
    membership = session.get(ProjectMember, {"project_id": project.id, "user_id": user.id})
    if membership is None:
        raise not_found("PROJECT_NOT_FOUND", "The requested project was not found.")

    document_statuses = [StatusCount(status=s, count=c) for s, c in session.execute(
        select(Document.status, func.count(Document.id)).where(Document.project_id == project.id).group_by(Document.status)
    )]
    document_map = {item.status: item.count for item in document_statuses}
    confirmed_document_ids = select(RecordParcelLink.document_id).where(
        RecordParcelLink.project_id == project.id, RecordParcelLink.link_status == "CONFIRMED"
    )
    unlinked_validated = session.scalar(select(func.count(Document.id)).where(
        Document.project_id == project.id, Document.status == "VALIDATED", ~Document.id.in_(confirmed_document_ids)
    )) or 0

    open_filters = (ReviewTask.project_id == project.id, ReviewTask.status == "OPEN")
    open_reviews = session.scalar(select(func.count(ReviewTask.id)).where(*open_filters)) or 0
    document_reviews = session.scalar(select(func.count(ReviewTask.id)).where(
        *open_filters, ReviewTask.queue_type == "DOCUMENT", ReviewTask.target_type != "RECORD_PARCEL_LINK"
    )) or 0
    gis_reviews = session.scalar(select(func.count(ReviewTask.id)).where(*open_filters, ReviewTask.queue_type == "GIS")) or 0
    link_reviews = session.scalar(select(func.count(ReviewTask.id)).where(
        *open_filters, ReviewTask.target_type == "RECORD_PARCEL_LINK"
    )) or 0
    high_reviews = session.scalar(select(func.count(ReviewTask.id)).where(*open_filters, ReviewTask.severity == "HIGH")) or 0
    medium_reviews = session.scalar(select(func.count(ReviewTask.id)).where(*open_filters, ReviewTask.severity == "MEDIUM")) or 0
    assigned_reviews = session.scalar(select(func.count(ReviewTask.id)).where(
        *open_filters, ReviewTask.assignee_user_id == user.id
    )) or 0
    validation_issues = session.scalar(select(func.count(ReviewTask.id)).where(
        *open_filters, ReviewTask.target_type == "VALIDATION_ISSUE"
    )) or 0

    parcel_statuses = [StatusCount(status=s, count=c) for s, c in session.execute(
        select(Parcel.status, func.count(Parcel.id)).where(Parcel.project_id == project.id).group_by(Parcel.status)
    )]
    parcel_map = {item.status: item.count for item in parcel_statuses}
    link_statuses = [StatusCount(status=s, count=c) for s, c in session.execute(
        select(RecordParcelLink.link_status, func.count(RecordParcelLink.id))
        .where(RecordParcelLink.project_id == project.id).group_by(RecordParcelLink.link_status)
    )]
    link_map = {item.status: item.count for item in link_statuses}
    job_statuses = [StatusCount(status=s, count=c) for s, c in session.execute(
        select(ProcessingJob.status, func.count(ProcessingJob.id))
        .where(ProcessingJob.project_id == project.id).group_by(ProcessingJob.status)
    )]
    job_map = {item.status: item.count for item in job_statuses}

    return ProjectDashboardResponse(
        project=_project_response(project),
        project_role=membership.role,
        documents=DashboardDocumentMetrics(
            total=sum(i.count for i in document_statuses), by_status=document_statuses,
            validated_records=document_map.get("VALIDATED", 0), unlinked_validated_records=unlinked_validated,
        ),
        reviews=DashboardReviewMetrics(
            open=open_reviews, document=document_reviews, gis=gis_reviews, record_parcel_link=link_reviews,
            high=high_reviews, medium=medium_reviews, assigned_to_me=assigned_reviews,
            validation_issues=validation_issues,
        ),
        geo=DashboardGeoMetrics(
            imagery_assets=session.scalar(select(func.count(ImageryAsset.id)).where(ImageryAsset.project_id == project.id)) or 0,
            geoai_jobs=session.scalar(select(func.count(GeoAIJob.id)).where(GeoAIJob.project_id == project.id)) or 0,
            parcels=session.scalar(select(func.count(Parcel.id)).where(Parcel.project_id == project.id)) or 0,
            buildings=session.scalar(select(func.count(Building.id)).where(Building.project_id == project.id)) or 0,
            roads=session.scalar(select(func.count(Road.id)).where(Road.project_id == project.id)) or 0,
            land_use_features=session.scalar(select(func.count(LandUseFeature.id)).where(LandUseFeature.project_id == project.id)) or 0,
            parcel_statuses=parcel_statuses,
        ),
        record_parcel_links=DashboardLinkMetrics(
            total=sum(i.count for i in link_statuses), by_status=link_statuses,
            confirmed_records=session.scalar(select(func.count(func.distinct(RecordParcelLink.document_id))).where(
                RecordParcelLink.project_id == project.id, RecordParcelLink.link_status == "CONFIRMED"
            )) or 0,
        ),
        jobs=DashboardJobMetrics(
            total=sum(i.count for i in job_statuses),
            by_status=job_statuses,
            active=job_map.get("QUEUED", 0) + job_map.get("PROCESSING", 0),
            failed=job_map.get("FAILED", 0),
            retryable_failed=session.scalar(
                select(func.count(ProcessingJob.id)).where(
                    ProcessingJob.project_id == project.id,
                    ProcessingJob.status == "FAILED",
                    ProcessingJob.job_type.in_((
                        "DOCUMENT_AI_PROCESS", "DOCUMENT_REVALIDATE", "PARCEL_IMPORT",
                        "BUILDING_VECTORIZE", "IMAGERY_REGISTER",
                    )),
                )
            ) or 0,
        ),
        attention=DashboardAttentionMetrics(
            failed_jobs=job_map.get("FAILED", 0),
            review_required_documents=document_map.get("REVIEW_REQUIRED", 0),
            high_open_reviews=high_reviews,
            ambiguous_record_parcel_links=link_map.get("REVIEW_REQUIRED", 0),
            parcels_needing_review=parcel_map.get("REVIEW_REQUIRED", 0),
            open_validation_issues=validation_issues,
        ),
        visibility=DashboardVisibilityPolicy(
            project_role=membership.role,
            draft_data_visible=True,
            viewer_read_only=membership.role == "VIEWER",
            notice=(
                "Viewer members may inspect draft and unverified evidence in read-only mode; "
                "verification labels remain visible and backend permissions block mutations."
                if membership.role == "VIEWER"
                else "Draft and unverified evidence remains explicitly labelled throughout the project workspace."
            ),
        ),
    )
