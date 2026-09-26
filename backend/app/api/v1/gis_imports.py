import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import get_current_user
from app.core.database import get_db_session
from app.core.errors import ApiError, not_found
from app.models import File, GeoAIJob, User
from app.models.gis_imports import GisImportRun, sync_gis_import_run
from app.schemas.gis_imports import (
    GeoPackageImportAcceptedResponse,
    GeoPackageImportCreateRequest,
    GeoPackageImportDetailResponse,
)
from app.services.file_policy import ALLOWED_CONTENT_TYPES, FileCategory
from app.services.processing_jobs import create_or_get_job
from app.services.project_access import get_project_for_user
from app.workers.tasks import process_geopackage_import

router = APIRouter(prefix="/projects/{project_id}/gis-imports", tags=["gis-imports"])


@router.post(
    "",
    response_model=GeoPackageImportAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_gis_import(
    project_id: uuid.UUID,
    request: GeoPackageImportCreateRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> GeoPackageImportAcceptedResponse:
    """Validate and enqueue a new GeoPackage GIS import run."""
    project = get_project_for_user(session, user, project_id, "geo:edit_draft")

    file_record = session.get(File, request.file_id)
    if file_record is None or file_record.project_id != project.id:
        raise not_found("FILE_NOT_FOUND", "The requested GIS import file was not found.")

    if file_record.category != "GIS_IMPORT":
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "GIS_IMPORT_FILE_INVALID",
            "File category must be GIS_IMPORT.",
        )

    if not file_record.original_name.lower().endswith(".gpkg"):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "GIS_IMPORT_FILE_INVALID",
            "GIS import file must have a .gpkg extension.",
        )

    allowed_types = ALLOWED_CONTENT_TYPES.get(FileCategory.GIS_IMPORT, frozenset())
    if file_record.mime_type not in allowed_types:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "GIS_IMPORT_FILE_INVALID",
            "File content type is not supported for GIS import.",
        )

    if file_record.status != "UPLOADED":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "FILE_UNAVAILABLE",
            "The GIS import file upload is not complete.",
        )

    # Repeat protection: reuse existing active import run if one is currently queued or processing
    active_run = session.scalar(
        select(GisImportRun).where(
            GisImportRun.project_id == project.id,
            GisImportRun.file_id == file_record.id,
            GisImportRun.status.in_(("QUEUED", "PROCESSING")),
        )
    )
    if active_run is not None:
        return GeoPackageImportAcceptedResponse(
            import_run_id=active_run.id,
            processing_job_id=active_run.processing_job_id,
            status=active_run.status,
        )

    job_idempotency_key = f"gis_import:{project.id}:{file_record.id}:{uuid.uuid4()}"
    job, created = create_or_get_job(session, project.id, "GEOPACKAGE_IMPORT", job_idempotency_key)

    geoai_job = GeoAIJob(
        id=job.id,
        project_id=project.id,
        job_type="GEOPACKAGE_IMPORT",
        requested_by_user_id=user.id,
        parameters_json={
            "file_id": str(file_record.id),
            "layer_mapping": request.layer_mapping,
            "import_mode": request.mode,
            "source_reference": request.source_reference,
        },
    )
    session.add(geoai_job)

    import_run = GisImportRun(
        id=uuid.uuid4(),
        project_id=project.id,
        file_id=file_record.id,
        processing_job_id=job.id,
        requested_by_user_id=user.id,
        source_reference=request.source_reference,
        layer_mapping_json=request.layer_mapping,
        status="QUEUED",
        detected_layers_json={},
        summary_json={},
    )
    session.add(import_run)

    record_audit(
        session,
        "gis_import.queued",
        "gis_import_run",
        import_run.id,
        actor_id=user.id,
        project_id=project.id,
        metadata={
            "file_id": str(file_record.id),
            "processing_job_id": str(job.id),
        },
    )
    session.commit()

    # Enqueue background processing
    process_geopackage_import.delay(str(job.id))

    return GeoPackageImportAcceptedResponse(
        import_run_id=import_run.id,
        processing_job_id=job.id,
        status=import_run.status,
    )


@router.get(
    "/{import_run_id}",
    response_model=GeoPackageImportDetailResponse,
)
def get_gis_import_detail(
    project_id: uuid.UUID,
    import_run_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> GeoPackageImportDetailResponse:
    """Retrieve details, status, and validation summary for a GeoPackage GIS import run."""
    project = get_project_for_user(session, user, project_id, "geo:edit_draft")

    import_run = session.get(GisImportRun, import_run_id)
    if import_run is None or import_run.project_id != project.id:
        raise not_found("GIS_IMPORT_NOT_FOUND", "The requested GIS import run was not found.")

    sync_gis_import_run(session, import_run)

    return GeoPackageImportDetailResponse(
        import_run_id=import_run.id,
        project_id=import_run.project_id,
        file_id=import_run.file_id,
        processing_job_id=import_run.processing_job_id,
        requested_by_user_id=import_run.requested_by_user_id,
        source_reference=import_run.source_reference,
        layer_mapping=import_run.layer_mapping_json,
        status=import_run.status,
        detected_layers=import_run.detected_layers_json,
        summary=import_run.summary_json,
        error_code=import_run.error_code,
        created_at=import_run.created_at or datetime.now(timezone.utc),
        updated_at=import_run.updated_at or datetime.now(timezone.utc),
    )
