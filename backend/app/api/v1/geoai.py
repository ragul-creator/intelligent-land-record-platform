"""Project-scoped C.7 GeoAI jobs and immutable parcel geometry APIs."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import get_current_user
from app.core.database import get_db_session
from app.core.errors import ApiError, not_found
from app.models import Building, GeoAIJob, LandUseFeature, Parcel, ParcelGeometryVersion, ProcessingJob, Road, TopologyError, User
from app.schemas.common import PageMetadata
from app.schemas.geoai import (
    GeoAIJobCreateRequest,
    GeoAIJobResponse,
    ParcelListResponse,
    ParcelResponse,
    ParcelVersionCreateRequest,
    ParcelVersionCreateResponse,
    ParcelVersionListResponse,
    ParcelVersionResponse,
    GeoFeatureListResponse,
    GeoFeatureResponse,
    TopologyErrorListResponse,
    TopologyErrorResponse,
)
from app.services.geoai import ParcelVersionConflict, GeoAIServiceError, create_human_parcel_version, current_version, geometry_geojson
from app.services.processing_jobs import InvalidJobTransition, create_or_get_job, mark_job_cancelled
from app.services.project_access import get_project_for_user
from app.workers.tasks import process_geoai_parcel_import


router = APIRouter(prefix="/projects/{project_id}", tags=["geoai"])


def _job_response(job: GeoAIJob, processing: ProcessingJob) -> GeoAIJobResponse:
    return GeoAIJobResponse(id=job.id, project_id=job.project_id, job_type=job.job_type, status=processing.status, progress=processing.progress, has_error=processing.error_json is not None, output_references=job.output_refs_json, created_at=job.created_at, updated_at=job.updated_at)


def _version_response(version: ParcelGeometryVersion) -> ParcelVersionResponse:
    return ParcelVersionResponse(id=version.id, version=version.version, geometry=geometry_geojson(version.geometry), source=version.source, source_reference=version.source_reference, coordinate_space=version.coordinate_space, source_crs=version.source_crs, area_m2=version.area_m2, area_sqft=version.area_sqft, change_reason=version.change_reason, validation_status=version.validation_status, created_by_user_id=version.created_by_user_id, created_by_type=version.created_by_type, processed_at=version.processed_at, created_at=version.created_at)


def _parcel_response(session: Session, parcel: Parcel) -> ParcelResponse:
    version = current_version(session, parcel)
    return ParcelResponse(id=parcel.id, project_id=parcel.project_id, external_identifier=parcel.external_identifier, source=parcel.source, source_reference=parcel.source_reference, status=parcel.status, verification_status=parcel.verification_status, current_geometry_version=parcel.current_geometry_version, coordinate_space=parcel.coordinate_space, source_crs=parcel.source_crs, confidence=parcel.confidence, model_version=parcel.model_version, ai_boundary_status=parcel.ai_boundary_status, requires_survey=parcel.requires_survey, current_version=_version_response(version), created_at=parcel.created_at, updated_at=parcel.updated_at)


@router.post("/geoai/jobs", response_model=GeoAIJobResponse, status_code=status.HTTP_202_ACCEPTED)
def create_geoai_job(project_id: uuid.UUID, request: GeoAIJobCreateRequest, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> GeoAIJobResponse:
    project = get_project_for_user(session, user, project_id, "geoai:process")
    key = request.idempotency_key or f"geoai:{request.job_type}:{uuid.uuid4()}"
    processing, created = create_or_get_job(session, project.id, request.job_type, key)
    geoai_job = session.get(GeoAIJob, processing.id)
    if created:
        geoai_job = GeoAIJob(id=processing.id, project_id=project.id, requested_by_user_id=user.id, job_type=request.job_type, parameters_json=request.model_dump(exclude={"idempotency_key"}))
        session.add(geoai_job)
        record_audit(session, "geoai.job_created", "geoai_job", geoai_job.id, actor_id=user.id, project_id=project.id, metadata={"job_type": request.job_type})
        session.commit()
        if request.job_type == "PARCEL_IMPORT":
            process_geoai_parcel_import.delay(str(geoai_job.id))
    elif geoai_job is None:
        raise ApiError(status.HTTP_409_CONFLICT, "GEOAI_IDEMPOTENCY_CONFLICT", "The idempotency key belongs to another processing workflow.")
    return _job_response(geoai_job, processing)


@router.get("/geoai/jobs/{job_id}", response_model=GeoAIJobResponse)
def get_geoai_job(project_id: uuid.UUID, job_id: uuid.UUID, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> GeoAIJobResponse:
    get_project_for_user(session, user, project_id, "geo:read")
    job = session.get(GeoAIJob, job_id)
    processing = session.get(ProcessingJob, job_id)
    if job is None or processing is None or job.project_id != project_id:
        raise not_found("GEOAI_JOB_NOT_FOUND", "The requested GeoAI job was not found.")
    return _job_response(job, processing)


@router.post("/geoai/jobs/{job_id}/cancel", response_model=GeoAIJobResponse)
def cancel_geoai_job(project_id: uuid.UUID, job_id: uuid.UUID, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> GeoAIJobResponse:
    get_project_for_user(session, user, project_id, "geoai:process")
    job = session.get(GeoAIJob, job_id)
    processing = session.get(ProcessingJob, job_id)
    if job is None or processing is None or job.project_id != project_id:
        raise not_found("GEOAI_JOB_NOT_FOUND", "The requested GeoAI job was not found.")
    try:
        mark_job_cancelled(session, processing)
    except InvalidJobTransition as error:
        raise ApiError(status.HTTP_409_CONFLICT, "GEOAI_JOB_NOT_CANCELLABLE", "The GeoAI job has already started or finished.") from error
    record_audit(session, "geoai.job_cancelled", "geoai_job", job.id, actor_id=user.id, project_id=project_id)
    session.commit()
    return _job_response(job, processing)


@router.get("/parcels", response_model=ParcelListResponse)
def list_parcels(project_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> ParcelListResponse:
    get_project_for_user(session, user, project_id, "geo:read")
    total = session.scalar(select(func.count(Parcel.id)).where(Parcel.project_id == project_id)) or 0
    parcels = list(session.scalars(select(Parcel).where(Parcel.project_id == project_id).order_by(Parcel.created_at.desc(), Parcel.id).limit(limit).offset(offset)))
    return ParcelListResponse(items=[_parcel_response(session, parcel) for parcel in parcels], page=PageMetadata(limit=limit, offset=offset, total=total))


def _page_features(session: Session, model: Any, project_id: uuid.UUID, limit: int, offset: int, response) -> GeoFeatureListResponse:
    total = session.scalar(select(func.count(model.id)).where(model.project_id == project_id)) or 0
    records = list(session.scalars(select(model).where(model.project_id == project_id).order_by(model.created_at.desc(), model.id).limit(limit).offset(offset)))
    return GeoFeatureListResponse(items=[response(record) for record in records], page=PageMetadata(limit=limit, offset=offset, total=total))


def _building_response(feature: Building) -> GeoFeatureResponse:
    return GeoFeatureResponse(id=feature.id, project_id=feature.project_id, geometry=geometry_geojson(feature.geometry), source=feature.source, source_reference=feature.source_reference, confidence=feature.confidence, model_version=feature.model_version, status=feature.status, verification_status=feature.verification_status, processed_at=feature.processed_at, properties={"area_m2": feature.area_m2, "area_sqft": feature.area_sqft})


def _road_response(feature: Road) -> GeoFeatureResponse:
    return GeoFeatureResponse(id=feature.id, project_id=feature.project_id, geometry=geometry_geojson(feature.geometry), source=feature.source, source_reference=feature.source_reference, confidence=feature.confidence, model_version=feature.model_version, status=feature.status, verification_status=feature.verification_status, processed_at=feature.processed_at, properties={"road_class": feature.road_class, "length_m": feature.length_m})


def _land_use_response(feature: LandUseFeature) -> GeoFeatureResponse:
    return GeoFeatureResponse(id=feature.id, project_id=feature.project_id, geometry=geometry_geojson(feature.geometry), source=feature.source, source_reference=feature.source_reference, confidence=feature.confidence, model_version=feature.model_version, status=feature.status, verification_status=feature.verification_status, processed_at=feature.processed_at, properties={"land_use_class": feature.land_use_class, "area_m2": feature.area_m2, "area_sqft": feature.area_sqft})


@router.get("/buildings", response_model=GeoFeatureListResponse)
def list_buildings(project_id: uuid.UUID, limit: int = Query(500, ge=1, le=1000), offset: int = Query(0, ge=0), session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> GeoFeatureListResponse:
    get_project_for_user(session, user, project_id, "geo:read")
    return _page_features(session, Building, project_id, limit, offset, _building_response)


@router.get("/roads", response_model=GeoFeatureListResponse)
def list_roads(project_id: uuid.UUID, limit: int = Query(500, ge=1, le=1000), offset: int = Query(0, ge=0), session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> GeoFeatureListResponse:
    get_project_for_user(session, user, project_id, "geo:read")
    return _page_features(session, Road, project_id, limit, offset, _road_response)


@router.get("/land-use", response_model=GeoFeatureListResponse)
def list_land_use(project_id: uuid.UUID, limit: int = Query(500, ge=1, le=1000), offset: int = Query(0, ge=0), session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> GeoFeatureListResponse:
    get_project_for_user(session, user, project_id, "geo:read")
    return _page_features(session, LandUseFeature, project_id, limit, offset, _land_use_response)


@router.get("/topology-errors", response_model=TopologyErrorListResponse)
def list_topology_errors(project_id: uuid.UUID, resolved: bool = False, limit: int = Query(500, ge=1, le=1000), offset: int = Query(0, ge=0), session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> TopologyErrorListResponse:
    get_project_for_user(session, user, project_id, "geo:read")
    filters = [TopologyError.project_id == project_id, TopologyError.resolved == resolved]
    total = session.scalar(select(func.count(TopologyError.id)).where(*filters)) or 0
    records = list(session.scalars(select(TopologyError).where(*filters).order_by(TopologyError.created_at.desc(), TopologyError.id).limit(limit).offset(offset)))
    return TopologyErrorListResponse(items=[TopologyErrorResponse(id=item.id, project_id=item.project_id, parcel_id=item.parcel_id, related_parcel_id=item.related_parcel_id, code=item.code, severity=item.severity, area_m2=item.area_m2, message=item.message, resolved=item.resolved, created_at=item.created_at) for item in records], page=PageMetadata(limit=limit, offset=offset, total=total))


def _project_parcel(session: Session, user: User, project_id: uuid.UUID, parcel_id: uuid.UUID, permission: str) -> Parcel:
    get_project_for_user(session, user, project_id, permission)
    parcel = session.get(Parcel, parcel_id)
    if parcel is None or parcel.project_id != project_id:
        raise not_found("PARCEL_NOT_FOUND", "The requested parcel was not found.")
    return parcel


@router.get("/parcels/{parcel_id}", response_model=ParcelResponse)
def get_parcel(project_id: uuid.UUID, parcel_id: uuid.UUID, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> ParcelResponse:
    return _parcel_response(session, _project_parcel(session, user, project_id, parcel_id, "geo:read"))


@router.get("/parcels/{parcel_id}/versions", response_model=ParcelVersionListResponse)
def list_parcel_versions(project_id: uuid.UUID, parcel_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> ParcelVersionListResponse:
    parcel = _project_parcel(session, user, project_id, parcel_id, "geo:read")
    total = session.scalar(select(func.count(ParcelGeometryVersion.id)).where(ParcelGeometryVersion.parcel_id == parcel.id)) or 0
    versions = list(session.scalars(select(ParcelGeometryVersion).where(ParcelGeometryVersion.parcel_id == parcel.id).order_by(ParcelGeometryVersion.version).limit(limit).offset(offset)))
    return ParcelVersionListResponse(items=[_version_response(version) for version in versions], page=PageMetadata(limit=limit, offset=offset, total=total))


@router.post("/parcels/{parcel_id}/versions", response_model=ParcelVersionCreateResponse, status_code=status.HTTP_201_CREATED)
def create_parcel_version(project_id: uuid.UUID, parcel_id: uuid.UUID, request: ParcelVersionCreateRequest, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> ParcelVersionCreateResponse:
    parcel = _project_parcel(session, user, project_id, parcel_id, "geo:edit_draft")
    try:
        version, result = create_human_parcel_version(session, parcel, user.id, request.geometry, request.source_crs, request.expected_current_version, request.change_reason)
    except ParcelVersionConflict as error:
        raise ApiError(status.HTTP_409_CONFLICT, "PARCEL_VERSION_CONFLICT", str(error)) from error
    except GeoAIServiceError as error:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "PARCEL_EDIT_INVALID", str(error)) from error
    record_audit(session, "parcel.geometry_version_created", "parcel_geometry_version", version.id, actor_id=user.id, project_id=project_id, metadata={"parcel_id": str(parcel.id), "version": version.version, "status": result.status})
    if result.issues:
        record_audit(session, "topology.validation_result", "parcel", parcel.id, actor_id=user.id, project_id=project_id, metadata={"issue_codes": [issue.code for issue in result.issues]})
    session.commit()
    return ParcelVersionCreateResponse(version=_version_response(version), status=result.status, area_before_m2=result.area_before_m2, area_after_m2=result.area_after_m2, issues=[{"code": issue.code, "severity": issue.severity, "message": issue.message, "area_m2": issue.area_m2} for issue in result.issues])
