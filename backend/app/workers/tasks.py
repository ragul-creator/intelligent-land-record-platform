"""Database-backed Celery tasks for registered files, GeoAI, and Document AI."""

import json
import os
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.storage import get_storage_service
from app.audit.service import record_audit
from app.models import Building, Document, DocumentProcessingJob, DocumentOcrResultRecord, File, GeoAIJob, ImageryAsset, LandUseFeature, Parcel, ParcelGeometryVersion, ProcessingJob, RecordParcelLink, Road, TopologyError
from app.services.geoai import GeoAIServiceError, _to_wgs84, persist_parcel_import
from app.core.config import get_settings
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import mapping, shape
from app.services.documents import extraction_from_records, persist_extraction, persist_ocr_result, persist_validation
from app.services.processing_jobs import mark_job_completed, mark_job_failed, mark_job_processing, mark_job_retry_queued
from app.services.review import create_review_task
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_file_registration(self, job_id: str) -> None:
    """Exercise worker persistence transitions for a completed file registration."""
    with SessionLocal() as session:
        job = session.get(ProcessingJob, uuid.UUID(job_id))
        if job is None or job.status != "QUEUED":
            return
        try:
            job.retry_count = max(job.retry_count or 0, self.request.retries)
            mark_job_processing(session, job)
            record_audit(session, "processing_job.started", "processing_job", job.id, project_id=job.project_id)
            session.commit()
            # Future OCR/GeoAI services replace this deliberately minimal handoff task.
            mark_job_completed(session, job)
            record_audit(session, "processing_job.completed", "processing_job", job.id, project_id=job.project_id)
            session.commit()
        except Exception as error:
            session.rollback()
            job = session.get(ProcessingJob, uuid.UUID(job_id))
            if job is not None and job.status == "PROCESSING":
                if self.request.retries < self.max_retries:
                    mark_job_retry_queued(session, job, max((job.retry_count or 0) + 1, self.request.retries + 1))
                    record_audit(session, "processing_job.retry_queued", "processing_job", job.id, project_id=job.project_id, metadata={"retry_count": job.retry_count})
                else:
                    mark_job_failed(session, job, str(error))
                    record_audit(session, "processing_job.failed", "processing_job", job.id, project_id=job.project_id)
                session.commit()
            raise


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_geoai_parcel_import(self, geoai_job_id: str) -> None:
    """Persist one C.4 parcel-import result as immutable PostGIS version 1."""
    job_uuid = uuid.UUID(geoai_job_id)
    with SessionLocal() as session:
        geoai_job = session.get(GeoAIJob, job_uuid)
        processing_job = session.get(ProcessingJob, job_uuid)
        if geoai_job is None or processing_job is None or processing_job.status != "QUEUED":
            return
        try:
            processing_job.retry_count = max(processing_job.retry_count or 0, self.request.retries)
            mark_job_processing(session, processing_job)
            record_audit(session, "geoai.job_processing", "geoai_job", geoai_job.id, project_id=geoai_job.project_id)
            session.commit()

            parcel = persist_parcel_import(session, geoai_job.project_id, geoai_job.parameters_json)
            session.flush()
            geoai_job.output_refs_json = {"parcel_id": str(parcel.id), "geometry_version": 1}
            mark_job_completed(session, processing_job)
            record_audit(session, "parcel.created", "parcel", parcel.id, project_id=geoai_job.project_id)
            record_audit(session, "parcel.geometry_version_created", "parcel_geometry_version", None, project_id=geoai_job.project_id, metadata={"parcel_id": str(parcel.id), "version": 1})
            record_audit(session, "geoai.job_completed", "geoai_job", geoai_job.id, project_id=geoai_job.project_id)
            session.commit()
        except Exception as error:
            session.rollback()
            processing_job = session.get(ProcessingJob, job_uuid)
            geoai_job = session.get(GeoAIJob, job_uuid)
            if processing_job is not None and processing_job.status == "PROCESSING":
                if isinstance(error, GeoAIServiceError) or self.request.retries >= self.max_retries:
                    mark_job_failed(session, processing_job, str(error))
                    if geoai_job is not None:
                        record_audit(session, "geoai.job_failed", "geoai_job", geoai_job.id, project_id=geoai_job.project_id)
                else:
                    mark_job_retry_queued(session, processing_job, max((processing_job.retry_count or 0) + 1, self.request.retries + 1))
                    if geoai_job is not None:
                        record_audit(session, "geoai.job_retry_queued", "geoai_job", geoai_job.id, project_id=geoai_job.project_id, metadata={"retry_count": processing_job.retry_count})
                session.commit()
            if isinstance(error, GeoAIServiceError):
                return
            raise


def _fail_geoai_job(session, job: ProcessingJob, geoai_job: GeoAIJob | None, message: str) -> None:
    mark_job_failed(session, job, message)
    if geoai_job is not None:
        record_audit(session, "geoai.job_failed", "geoai_job", geoai_job.id, project_id=geoai_job.project_id, metadata={"reason": message})


@celery_app.task(bind=True)
def process_imagery_registration(self, job_id: str, imagery_asset_id: str) -> None:
    """Inspect a private GeoTIFF and create a private PNG preview in the GeoAI runtime."""
    job_uuid, asset_uuid = uuid.UUID(job_id), uuid.UUID(imagery_asset_id)
    with SessionLocal() as session:
        job, asset = session.get(ProcessingJob, job_uuid), session.get(ImageryAsset, asset_uuid)
        if job is None or asset is None or job.status != "QUEUED":
            return
        try:
            mark_job_processing(session, job)
            session.commit()
            source = session.get(File, asset.file_id)
            if source is None or source.project_id != asset.project_id:
                raise GeoAIServiceError("The imagery source is unavailable in this project.")
            metadata = dict(asset.metadata_json or {})
            metadata["registration_status"] = "PROCESSING"
            asset.metadata_json = metadata
            session.commit()
            source_bytes = get_storage_service().read_private_object(source.storage_key)
            with tempfile.NamedTemporaryFile(suffix=Path(source.original_name).suffix or ".tif", delete=False) as temporary:
                temporary.write(source_bytes)
                source_path = Path(temporary.name)
            try:
                from ai.geoai.runtime.imagery import inspect_and_render_preview

                inspected, preview_png, preview_corners = inspect_and_render_preview(source_path)
            finally:
                source_path.unlink(missing_ok=True)
            preview_key = f"projects/{asset.project_id}/derived/imagery/{asset.id}/preview.png"
            get_storage_service().put_derived_bytes(preview_key, preview_png, content_type="image/png")
            metadata = dict(inspected)
            metadata.update({"registration_status": "READY", "registration_job_id": str(job.id), "preview_storage_key": preview_key, "preview_corners_wgs84": preview_corners})
            asset.metadata_json = metadata
            asset.source_crs = str(metadata.get("source_crs") or metadata.get("crs_wkt") or "") or None
            asset.coordinate_space = "WORLD"
            job.progress = 100
            mark_job_completed(session, job)
            record_audit(session, "imagery.registered", "imagery_asset", asset.id, project_id=asset.project_id, metadata={"file_id": str(source.id), "source_crs": asset.source_crs})
            session.commit()
        except Exception as error:
            session.rollback()
            job, asset = session.get(ProcessingJob, job_uuid), session.get(ImageryAsset, asset_uuid)
            if job is not None and job.status == "PROCESSING":
                _fail_geoai_job(session, job, None, str(error))
                if asset is not None:
                    metadata = dict(asset.metadata_json or {})
                    metadata["registration_status"] = "FAILED"
                    asset.metadata_json = metadata
                    record_audit(session, "imagery.registration_failed", "imagery_asset", asset.id, project_id=asset.project_id)
                session.commit()
            return


@celery_app.task(bind=True)
def process_geoai_buildings(self, geoai_job_id: str) -> None:
    """Run the real C.2/C.3 building pipeline; never fabricate a model result."""
    job_uuid = uuid.UUID(geoai_job_id)
    with SessionLocal() as session:
        geoai_job, job = session.get(GeoAIJob, job_uuid), session.get(ProcessingJob, job_uuid)
        if geoai_job is None or job is None or job.status != "QUEUED":
            return
        try:
            mark_job_processing(session, job)
            session.commit()
            checkpoint = get_settings().geoai_building_checkpoint
            if not checkpoint or not Path(checkpoint).is_file():
                raise GeoAIServiceError("Building GeoAI is unavailable: GEOAI_BUILDING_CHECKPOINT is not configured to a readable checkpoint.")
            asset = session.get(ImageryAsset, geoai_job.imagery_asset_id)
            source = session.get(File, asset.file_id) if asset else None
            if asset is None or source is None or asset.project_id != geoai_job.project_id:
                raise GeoAIServiceError("The requested imagery asset is unavailable in this project.")
            source_bytes = get_storage_service().read_private_object(source.storage_key)
            with tempfile.NamedTemporaryFile(suffix=Path(source.original_name).suffix or ".tif", delete=False) as temporary:
                temporary.write(source_bytes)
                source_path = Path(temporary.name)
            try:
                from ai.geoai.runtime.buildings import infer_and_vectorize_geotiff

                result = infer_and_vectorize_geotiff(source_path, checkpoint=Path(checkpoint), device=get_settings().geoai_device, source_image=f"imagery:{asset.id}")
            finally:
                source_path.unlink(missing_ok=True)
            if result.coordinate_space != "WORLD" or not result.source_crs:
                raise GeoAIServiceError("Building processing requires georeferenced imagery; pixel-space outputs were not persisted.")

            # Serialize replacements for this imagery so concurrent/retried jobs
            # cannot leave multiple unverified AI candidate sets behind.
            session.execute(
                select(ImageryAsset.id)
                .where(
                    ImageryAsset.id == asset.id,
                    ImageryAsset.project_id == geoai_job.project_id,
                )
                .with_for_update()
            ).scalar_one()
            source_reference = f"imagery:{asset.id}"
            session.execute(
                delete(Building).where(
                    Building.project_id == geoai_job.project_id,
                    Building.source == "AI_CANDIDATE",
                    Building.source_reference == source_reference,
                    Building.status == "AI_PRELIMINARY",
                    Building.verification_status == "UNVERIFIED",
                )
            )

            created_ids: list[str] = []
            for feature in result.features:
                building = Building(
                    project_id=geoai_job.project_id,
                    geometry=from_shape(_to_wgs84(feature.geometry, result.source_crs), srid=4326),
                    source="AI_CANDIDATE",
                    source_reference=source_reference,
                    confidence=feature.confidence,
                    model_version=feature.model_version,
                    status="AI_PRELIMINARY",
                    verification_status="UNVERIFIED",
                    area_m2=feature.area_m2,
                    area_sqft=feature.area_sqft,
                    processed_at=datetime.fromisoformat(feature.processed_at.replace("Z", "+00:00")),
                )
                session.add(building)
                session.flush()
                created_ids.append(str(building.id))
            model_version = result.features[0].model_version if result.features else None
            geoai_job.output_refs_json = {"imagery_asset_id": str(asset.id), "building_ids": created_ids, "feature_count": len(created_ids), "model_version": model_version}
            geoai_job.metrics_json = {"feature_count": len(created_ids), "model_version": model_version, "processing_parameters": result.processing_parameters}
            job.progress = 100
            mark_job_completed(session, job)
            record_audit(session, "geoai.buildings_created", "geoai_job", geoai_job.id, project_id=geoai_job.project_id, metadata={"imagery_asset_id": str(asset.id), "feature_count": len(created_ids)})
            session.commit()
        except Exception as error:
            print(f"BUILDING_GEOAI_ERROR: {type(error).__name__}: {error}", flush=True)
            session.rollback()
            job, geoai_job = session.get(ProcessingJob, job_uuid), session.get(GeoAIJob, job_uuid)
            if job is not None and job.status == "PROCESSING":
                _fail_geoai_job(session, job, geoai_job, str(error))
                session.commit()
            return


@celery_app.task(bind=True, autoretry_for=(urllib.error.URLError,), retry_backoff=True, max_retries=3)
def process_geoai_roads(self, geoai_job_id: str) -> None:
    """Run H.2B.5 road inference; missing road model configuration is terminal and safe."""
    job_uuid = uuid.UUID(geoai_job_id)
    with SessionLocal() as session:
        geoai_job, job = session.get(GeoAIJob, job_uuid), session.get(ProcessingJob, job_uuid)
        if geoai_job is None or job is None or job.status != "QUEUED":
            return
        try:
            mark_job_processing(session, job)
            session.commit()
            settings = get_settings()
            checkpoint = settings.geoai_road_checkpoint
            samroad_service_url = settings.samroad_service_url

            if not samroad_service_url and (not checkpoint or not Path(checkpoint).is_file()):
                raise GeoAIServiceError(
                    "Road GeoAI is unavailable: configure SAMROAD_SERVICE_URL "
                    "or GEOAI_ROAD_CHECKPOINT."
                )
            asset = session.get(ImageryAsset, geoai_job.imagery_asset_id)
            source = session.get(File, asset.file_id) if asset else None
            if asset is None or source is None or asset.project_id != geoai_job.project_id:
                raise GeoAIServiceError("The requested imagery asset is unavailable in this project.")
            source_bytes = get_storage_service().read_private_object(source.storage_key)
            with tempfile.NamedTemporaryFile(suffix=Path(source.original_name).suffix or ".tif", delete=False) as temporary:
                temporary.write(source_bytes)
                source_path = Path(temporary.name)
            try:
                samroad_payload = None

                if samroad_service_url:
                    request = urllib.request.Request(
                        samroad_service_url.rstrip("/") + "/infer",
                        data=source_bytes,
                        headers={"Content-Type": "image/tiff"},
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=900) as response:
                        samroad_payload = json.load(response)

                    result = None
                else:
                    from ai.geoai.runtime.roads import infer_and_vectorize_geotiff

                    result = infer_and_vectorize_geotiff(
                        source_path,
                        checkpoint=checkpoint,
                        device=settings.geoai_device,
                        threshold=settings.geoai_road_threshold,
                    )
            finally:
                source_path.unlink(missing_ok=True)

            # Match building rerun semantics: keep reviewed/manual roads, but
            # replace stale unverified AI candidates for this exact imagery.
            session.execute(
                select(ImageryAsset.id)
                .where(
                    ImageryAsset.id == asset.id,
                    ImageryAsset.project_id == geoai_job.project_id,
                )
                .with_for_update()
            ).scalar_one()
            source_reference = f"imagery:{asset.id}"
            session.execute(
                delete(Road).where(
                    Road.project_id == geoai_job.project_id,
                    Road.source == "AI_CANDIDATE",
                    Road.source_reference == source_reference,
                    Road.status == "AI_PRELIMINARY",
                    Road.verification_status == "UNVERIFIED",
                )
            )

            created_ids: list[str] = []
            road_diagnostics: dict[str, dict[str, object]] = {}
            flagged_road_ids: list[str] = []

            if samroad_payload is not None:
                samroad_features = samroad_payload.get("features", [])

                for feature in samroad_features:
                    properties = feature.get("properties") or {}
                    road_geometry = shape(feature["geometry"])

                    road = Road(
                        project_id=geoai_job.project_id,
                        geometry=from_shape(road_geometry, srid=4326),
                        road_class="ROAD",
                        source="AI_CANDIDATE",
                        source_reference=source_reference,
                        confidence=properties.get("confidence"),
                        model_version=properties.get(
                            "model_version",
                            samroad_payload.get("model_version", "sam-road"),
                        ),
                        status="AI_PRELIMINARY",
                        verification_status="UNVERIFIED",
                        length_m=None,
                        processed_at=datetime.now(timezone.utc),
                    )
                    session.add(road)
                    session.flush()
                    road_id = str(road.id)
                    created_ids.append(road_id)
                    diagnostic = {
                        "confidence": properties.get("confidence"),
                        "graph_support_fraction": properties.get("graph_support_fraction"),
                        "graph_mean_distance_px": properties.get("graph_mean_distance_px"),
                        "graph_median_distance_px": properties.get("graph_median_distance_px"),
                        "median_mask_width_px": properties.get("median_mask_width_px"),
                        "median_mask_width_m": properties.get("median_mask_width_m"),
                        "topology_disagreement_flag": bool(properties.get("topology_disagreement_flag", False)),
                        "mean_abs_recenter_shift_px": properties.get("mean_abs_recenter_shift_px"),
                        "max_abs_recenter_shift_px": properties.get("max_abs_recenter_shift_px"),
                        "recenter_mask_midpoint": properties.get("recenter_mask_midpoint"),
                        "recenter_only_disagreement": properties.get("recenter_only_disagreement"),
                    }
                    road_diagnostics[road_id] = diagnostic
                    if diagnostic["topology_disagreement_flag"]:
                        flagged_road_ids.append(road_id)

                geoai_job.metrics_json = {
                    "engine": "sam-road",
                    "target_gsd_m": samroad_payload.get("target_gsd_m"),
                    "node_count": samroad_payload.get("node_count"),
                    "raw_edge_count": samroad_payload.get("raw_edge_count"),
                    "feature_count": len(created_ids),
                    "topology_disagreement_count": len(flagged_road_ids),
                    "vectorization": samroad_payload.get("vectorization"),
                }
                geoai_job.output_refs_json = {
                    "imagery_asset_id": str(asset.id),
                    "road_ids": created_ids,
                    "feature_count": len(created_ids),
                    "flagged_road_ids": flagged_road_ids,
                    "road_diagnostics": road_diagnostics,
                    "model_version": samroad_payload.get(
                        "model_version",
                        (
                            (samroad_features[0].get("properties") or {}).get("model_version")
                            if samroad_features
                            else "sam-road"
                        ),
                    ),
                }

            else:
                for feature in result.features:
                    road = Road(
                        project_id=geoai_job.project_id,
                        geometry=from_shape(
                            _to_wgs84(feature.geometry, result.source_crs),
                            srid=4326,
                        ),
                        road_class="ROAD",
                        source="AI_CANDIDATE",
                        source_reference=source_reference,
                        confidence=feature.confidence,
                        model_version=feature.model_version,
                        status="AI_PRELIMINARY",
                        verification_status="UNVERIFIED",
                        length_m=feature.length_m,
                        processed_at=datetime.fromisoformat(
                            feature.processed_at.replace("Z", "+00:00")
                        ),
                    )
                    session.add(road)
                    session.flush()
                    created_ids.append(str(road.id))

                geoai_job.metrics_json = result.processing_parameters
                geoai_job.output_refs_json = {
                    "imagery_asset_id": str(asset.id),
                    "road_ids": created_ids,
                    "feature_count": len(created_ids),
                    "model_version": (
                        result.features[0].model_version
                        if result.features
                        else None
                    ),
                }
            job.progress = 100
            mark_job_completed(session, job)
            record_audit(session, "geoai.roads_created", "geoai_job", geoai_job.id, project_id=geoai_job.project_id, metadata={"imagery_asset_id": str(asset.id), "feature_count": len(created_ids)})
            session.commit()
        except Exception as error:
            print(f"ROAD_GEOAI_ERROR: {type(error).__name__}: {error}", flush=True)
            session.rollback()
            job, geoai_job = session.get(ProcessingJob, job_uuid), session.get(GeoAIJob, job_uuid)
            is_transient_service_error = isinstance(error, urllib.error.URLError)
            if job is not None and job.status == "PROCESSING":
                if is_transient_service_error and self.request.retries < self.max_retries:
                    mark_job_retry_queued(
                        session,
                        job,
                        max((job.retry_count or 0) + 1, self.request.retries + 1),
                    )
                    if geoai_job is not None:
                        record_audit(
                            session,
                            "geoai.job_retry_queued",
                            "geoai_job",
                            geoai_job.id,
                            project_id=geoai_job.project_id,
                            metadata={"retry_count": job.retry_count, "reason": str(error)},
                        )
                else:
                    _fail_geoai_job(session, job, geoai_job, str(error))
                session.commit()
            if is_transient_service_error:
                raise
            return


@celery_app.task(bind=True)
def process_geoai_parcel_delineation(self, geoai_job_id: str) -> None:
    """Generate preliminary plot candidates from current building and road evidence."""
    job_uuid = uuid.UUID(geoai_job_id)
    with SessionLocal() as session:
        geoai_job, job = session.get(GeoAIJob, job_uuid), session.get(ProcessingJob, job_uuid)
        if geoai_job is None or job is None or job.status != "QUEUED":
            return
        try:
            mark_job_processing(session, job)
            session.commit()

            asset = session.get(ImageryAsset, geoai_job.imagery_asset_id)
            source = session.get(File, asset.file_id) if asset else None
            if asset is None or source is None or asset.project_id != geoai_job.project_id:
                raise GeoAIServiceError("The requested imagery asset is unavailable in this project.")

            imagery_reference = f"imagery:{asset.id}"
            buildings = session.scalars(
                select(Building).where(
                    Building.project_id == geoai_job.project_id,
                    Building.source_reference == imagery_reference,
                    Building.geometry.is_not(None),
                )
            ).all()
            roads = session.scalars(
                select(Road).where(
                    Road.project_id == geoai_job.project_id,
                    Road.source_reference == imagery_reference,
                    Road.geometry.is_not(None),
                )
            ).all()
            if not buildings:
                raise GeoAIServiceError(
                    "Parcel delineation needs building evidence for this imagery. Run Building GeoAI first."
                )
            if not roads:
                raise GeoAIServiceError(
                    "Parcel delineation needs road evidence for this imagery. Run Road GeoAI first."
                )

            source_bytes = get_storage_service().read_private_object(source.storage_key)
            with tempfile.NamedTemporaryFile(
                suffix=Path(source.original_name).suffix or ".tif",
                delete=False,
            ) as temporary:
                temporary.write(source_bytes)
                source_path = Path(temporary.name)
            try:
                from ai.geoai.runtime.parcel_candidates import (
                    MODEL_VERSION,
                    SpatialFeature,
                    generate_parcel_candidates,
                )

                result = generate_parcel_candidates(
                    source_path,
                    buildings=[
                        SpatialFeature(str(item.id), to_shape(item.geometry))
                        for item in buildings
                    ],
                    roads=[
                        SpatialFeature(str(item.id), to_shape(item.geometry))
                        for item in roads
                    ],
                )
            finally:
                source_path.unlink(missing_ok=True)

            session.execute(
                select(ImageryAsset.id)
                .where(
                    ImageryAsset.id == asset.id,
                    ImageryAsset.project_id == geoai_job.project_id,
                )
                .with_for_update()
            ).scalar_one()

            candidate_reference = f"imagery:{asset.id}:parcel-candidate"
            stale = session.scalars(
                select(Parcel).where(
                    Parcel.project_id == geoai_job.project_id,
                    Parcel.source == "AI_VISIBLE_BOUNDARY",
                    Parcel.source_reference == candidate_reference,
                    Parcel.verification_status == "UNVERIFIED",
                )
            ).all()
            removed_count = 0
            preserved_count = 0
            for parcel in stale:
                linked = session.scalar(
                    select(RecordParcelLink.id)
                    .where(RecordParcelLink.parcel_id == parcel.id)
                    .limit(1)
                )
                topology = session.scalar(
                    select(TopologyError.id)
                    .where(
                        (TopologyError.parcel_id == parcel.id)
                        | (TopologyError.related_parcel_id == parcel.id)
                    )
                    .limit(1)
                )
                if parcel.current_geometry_version != 1 or linked is not None or topology is not None:
                    preserved_count += 1
                    continue
                session.execute(
                    delete(ParcelGeometryVersion).where(ParcelGeometryVersion.parcel_id == parcel.id)
                )
                session.delete(parcel)
                removed_count += 1
            session.flush()

            existing_identifiers = {
                value
                for value in session.scalars(
                    select(Parcel.external_identifier).where(
                        Parcel.project_id == geoai_job.project_id,
                        Parcel.external_identifier.is_not(None),
                    )
                )
                if value
            }
            next_number = 1001

            def next_identifier() -> str:
                nonlocal next_number
                while f"P-{next_number}" in existing_identifiers:
                    next_number += 1
                value = f"P-{next_number}"
                existing_identifiers.add(value)
                next_number += 1
                return value

            boundary_evidence_model_version = str(
                result.processing_parameters.get(
                    "boundary_evidence_model_version",
                    "visible-boundary-evidence-v1",
                )
            )

            def summarise_candidate_boundary_evidence(segments) -> dict[str, object]:
                lengths: dict[str, float] = {}
                weighted: dict[str, float] = {}
                counts: dict[str, int] = {}
                for segment in segments:
                    kind = str(segment.evidence_type)
                    length = float(segment.length_m)
                    lengths[kind] = lengths.get(kind, 0.0) + length
                    weighted[kind] = weighted.get(kind, 0.0) + float(segment.confidence) * length
                    counts[kind] = counts.get(kind, 0) + 1
                total = sum(lengths.values())
                supported = sum(length for kind, length in lengths.items() if kind != "GEOMETRY_ONLY")
                by_type = {
                    kind: {
                        "segment_count": counts[kind],
                        "length_m": round(length, 3),
                        "fraction": round(length / total, 4) if total else 0.0,
                        "mean_confidence": round(weighted[kind] / length, 4) if length else 0.0,
                    }
                    for kind, length in sorted(lengths.items())
                }
                return {
                    "primary_type": max(lengths, key=lengths.get) if lengths else "GEOMETRY_ONLY",
                    "edge_count": sum(counts.values()),
                    "total_length_m": round(total, 3),
                    "supported_fraction": round(supported / total, 4) if total else 0.0,
                    "by_type": by_type,
                }

            created_ids: list[str] = []
            created_identifiers: list[str] = []
            low_confidence_count = 0
            no_frontage_count = 0
            for index, candidate in enumerate(result.candidates, start=1):
                identifier = next_identifier()
                confidence_tier = (
                    "HIGH"
                    if candidate.confidence >= 0.80 and candidate.road_frontage_m >= 3.0
                    else "MEDIUM"
                    if candidate.confidence >= 0.65
                    else "LOW"
                )
                candidate_kind = (
                    "BUILDING_ASSOCIATED"
                    if candidate.building_ids
                    else "VACANT_OPEN_REVIEW"
                )
                candidate_boundary_evidence = tuple(
                    getattr(candidate, "boundary_evidence", ())
                )
                boundary_summary = summarise_candidate_boundary_evidence(candidate_boundary_evidence)
                boundary_segments = [
                    {
                        "evidence_type": segment.evidence_type,
                        "confidence": round(segment.confidence, 4),
                        "length_m": round(segment.length_m, 3),
                        "support_fraction": round(segment.support_fraction, 4),
                        "geometry": mapping(segment.geometry),
                    }
                    for segment in candidate_boundary_evidence
                ]
                properties = {
                    "candidate_index": index,
                    "candidate_kind": candidate_kind,
                    "building_ids": list(candidate.building_ids),
                    "building_count": len(candidate.building_ids),
                    "road_frontage_m": round(candidate.road_frontage_m, 3),
                    "frontage_supported": candidate.road_frontage_m >= 1.0,
                    "block_index": candidate.block_index,
                    "method": (
                        "ROAD_FRONTAGE_STRIPS"
                        if candidate.building_ids
                        else "ROAD_BLOCK_REMAINDER"
                    ),
                    "confidence_tier": confidence_tier,
                    "requires_review": (
                        candidate_kind == "VACANT_OPEN_REVIEW"
                        or confidence_tier == "LOW"
                    ),
                    "warnings": list(candidate.warnings),
                    "requires_survey": True,
                    "boundary_evidence_model_version": boundary_evidence_model_version,
                    "boundary_evidence_summary": boundary_summary,
                    "boundary_evidence": boundary_segments,
                }
                payload = {
                    "type": "Feature",
                    "geometry": mapping(candidate.geometry),
                    "properties": properties,
                    "parcel_id": identifier,
                    "evidence_type": str(boundary_summary["primary_type"]),
                    "confidence": candidate.confidence,
                    "model_version": MODEL_VERSION,
                    "notes": list(candidate.warnings),
                }
                parcel = persist_parcel_import(
                    session,
                    geoai_job.project_id,
                    {
                        "source_type": "AI_VISIBLE_BOUNDARY",
                        "source_payload": payload,
                        "source_crs": "EPSG:4326",
                        "source_reference": candidate_reference,
                        "model_version": MODEL_VERSION,
                    },
                )
                created_ids.append(str(parcel.id))
                created_identifiers.append(identifier)
                if candidate.confidence < 0.60:
                    low_confidence_count += 1
                if candidate.road_frontage_m < 1.0:
                    no_frontage_count += 1

            asset.metadata_json = {
                **(asset.metadata_json or {}),
                "parcel_candidate_count": len(created_ids),
                "parcel_candidate_model_version": MODEL_VERSION,
                "parcel_candidate_updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            geoai_job.output_refs_json = {
                "imagery_asset_id": str(asset.id),
                "parcel_ids": created_ids,
                "parcel_identifiers": created_identifiers,
                "feature_count": len(created_ids),
                "model_version": MODEL_VERSION,
            }
            geoai_job.metrics_json = {
                **result.processing_parameters,
                "feature_count": len(created_ids),
                "removed_stale_candidates": removed_count,
                "preserved_reviewed_candidates": preserved_count,
                "low_confidence_count": low_confidence_count,
                "no_frontage_count": no_frontage_count,
            }
            job.progress = 100
            mark_job_completed(session, job)
            record_audit(
                session,
                "geoai.parcel_candidates_created",
                "geoai_job",
                geoai_job.id,
                project_id=geoai_job.project_id,
                metadata={
                    "imagery_asset_id": str(asset.id),
                    "feature_count": len(created_ids),
                    "low_confidence_count": low_confidence_count,
                    "no_frontage_count": no_frontage_count,
                },
            )
            session.commit()
        except Exception as error:
            print(f"PARCEL_DELINEATION_ERROR: {type(error).__name__}: {error}", flush=True)
            session.rollback()
            job, geoai_job = session.get(ProcessingJob, job_uuid), session.get(GeoAIJob, job_uuid)
            if job is not None and job.status == "PROCESSING":
                _fail_geoai_job(session, job, geoai_job, str(error))
                session.commit()
            return


@celery_app.task(bind=True)
def process_geoai_land_use(self, geoai_job_id: str) -> None:
    """Run SegFormer-B2 LULC inference on compatible registered RGB+NIR imagery."""
    job_uuid = uuid.UUID(geoai_job_id)
    with SessionLocal() as session:
        geoai_job, job = session.get(GeoAIJob, job_uuid), session.get(ProcessingJob, job_uuid)
        if geoai_job is None or job is None or job.status != "QUEUED":
            return
        try:
            mark_job_processing(session, job)
            session.commit()
            settings = get_settings()
            model_dir = settings.geoai_lulc_model_dir
            if not model_dir or not Path(model_dir).is_dir():
                raise GeoAIServiceError(
                    "Land-use GeoAI is unavailable: GEOAI_LULC_MODEL_DIR is not configured "
                    "to a readable SegFormer model directory."
                )

            asset = session.get(ImageryAsset, geoai_job.imagery_asset_id)
            source = session.get(File, asset.file_id) if asset else None
            if asset is None or source is None or asset.project_id != geoai_job.project_id:
                raise GeoAIServiceError("The requested imagery asset is unavailable in this project.")

            source_bytes = get_storage_service().read_private_object(source.storage_key)
            with tempfile.NamedTemporaryFile(
                suffix=Path(source.original_name).suffix or ".tif",
                delete=False,
            ) as temporary:
                temporary.write(source_bytes)
                source_path = Path(temporary.name)
            try:
                from ai.geoai.runtime.lulc import infer_and_vectorize_geotiff

                result = infer_and_vectorize_geotiff(
                    source_path,
                    model_dir=Path(model_dir),
                    device=settings.geoai_device,
                    min_area_m2=settings.geoai_lulc_min_area_m2,
                    min_gsd_m=settings.geoai_lulc_min_gsd_m,
                    max_gsd_m=settings.geoai_lulc_max_gsd_m,
                )
            finally:
                source_path.unlink(missing_ok=True)

            if not result.source_crs:
                raise GeoAIServiceError(
                    "Land-use processing requires georeferenced imagery; pixel-space outputs were not persisted."
                )

            session.execute(
                select(ImageryAsset.id)
                .where(
                    ImageryAsset.id == asset.id,
                    ImageryAsset.project_id == geoai_job.project_id,
                )
                .with_for_update()
            ).scalar_one()
            source_reference = f"imagery:{asset.id}"
            session.execute(
                delete(LandUseFeature).where(
                    LandUseFeature.project_id == geoai_job.project_id,
                    LandUseFeature.source == "AI_CANDIDATE",
                    LandUseFeature.source_reference == source_reference,
                    LandUseFeature.status == "AI_PRELIMINARY",
                    LandUseFeature.verification_status == "UNVERIFIED",
                )
            )

            created_ids: list[str] = []
            classes: list[str] = []
            for feature in result.features:
                land_use = LandUseFeature(
                    project_id=geoai_job.project_id,
                    geometry=from_shape(
                        _to_wgs84(feature.geometry, result.source_crs),
                        srid=4326,
                    ),
                    land_use_class=feature.land_use_class,
                    source="AI_CANDIDATE",
                    source_reference=source_reference,
                    confidence=feature.confidence,
                    model_version=feature.model_version,
                    status="AI_PRELIMINARY",
                    verification_status="UNVERIFIED",
                    area_m2=feature.area_m2,
                    area_sqft=feature.area_sqft,
                    processed_at=datetime.fromisoformat(
                        feature.processed_at.replace("Z", "+00:00")
                    ),
                )
                session.add(land_use)
                session.flush()
                created_ids.append(str(land_use.id))
                classes.append(feature.land_use_class)

            model_version = result.features[0].model_version if result.features else None
            class_distribution = result.processing_parameters.get("class_distribution", [])
            asset.metadata_json = {
                **(asset.metadata_json or {}),
                "lulc_model_version": model_version,
                "lulc_valid_pixel_count": result.processing_parameters.get("valid_pixel_count", 0),
                "lulc_distribution": class_distribution,
                "lulc_updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            geoai_job.output_refs_json = {
                "imagery_asset_id": str(asset.id),
                "land_use_ids": created_ids,
                "feature_count": len(created_ids),
                "classes": classes,
                "model_version": model_version,
            }
            geoai_job.metrics_json = {
                **result.processing_parameters,
                "feature_count": len(created_ids),
                "classes": classes,
            }
            job.progress = 100
            mark_job_completed(session, job)
            record_audit(
                session,
                "geoai.land_use_created",
                "geoai_job",
                geoai_job.id,
                project_id=geoai_job.project_id,
                metadata={
                    "imagery_asset_id": str(asset.id),
                    "feature_count": len(created_ids),
                    "classes": classes,
                },
            )
            session.commit()
        except Exception as error:
            print(f"LULC_GEOAI_ERROR: {type(error).__name__}: {error}", flush=True)
            session.rollback()
            job, geoai_job = session.get(ProcessingJob, job_uuid), session.get(GeoAIJob, job_uuid)
            if job is not None and job.status == "PROCESSING":
                _fail_geoai_job(session, job, geoai_job, str(error))
                session.commit()
            return


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_document_ai(self, job_id: str) -> None:
    """Run F.1 -> F.2 -> F.3 without exposing or mutating the source object."""
    job_uuid = uuid.UUID(job_id)
    with SessionLocal() as session:
        job = session.get(ProcessingJob, job_uuid)
        detail = session.get(DocumentProcessingJob, job_uuid)
        if job is None or detail is None or job.status != "QUEUED":
            return
        document = session.get(Document, detail.document_id)
        if document is None:
            return
        try:
            job.retry_count = self.request.retries
            mark_job_processing(session, job)
            document.status = "PROCESSING"
            record_audit(session, "document.processing_started", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id)})
            session.commit()

            file = session.get(File, document.file_id)
            if file is None:
                raise RuntimeError("Document source artifact is unavailable.")
            source_bytes = get_storage_service().read_private_object(file.storage_key)
            suffix = Path(file.original_name).suffix or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
                temporary.write(source_bytes)
                source_path = Path(temporary.name)
            try:
                from ai.document_ai.extraction import OllamaEvidenceExtractor, extract_land_record_fields, merge_missing_fields
                from ai.document_ai.pipeline import OcrPipeline

                result = OcrPipeline().process(source_path, source_id=str(document.id), languages=detail.requested_languages_json, allowed_root=source_path.parent)
            finally:
                source_path.unlink(missing_ok=True)

            ocr = persist_ocr_result(session, document=document, job=job, detail=detail, result=result)
            extraction = extract_land_record_fields(result)
            if os.getenv("DOCUMENT_LLM_ENABLED", "true").lower() in {"1", "true", "yes", "on"}:
                try:
                    llm_url = os.getenv("DOCUMENT_LLM_URL", "http://host.docker.internal:11434/api/generate")
                    llm_model = os.getenv("DOCUMENT_LLM_MODEL", "qwen3:4b")
                    llm_extraction = OllamaEvidenceExtractor(model=llm_model, url=llm_url).extract(result)
                    extraction = merge_missing_fields(extraction, llm_extraction)
                except Exception as llm_error:
                    record_audit(session, "document.llm_fallback_unavailable", "document", document.id,
                                 project_id=document.project_id, metadata={"error": str(llm_error)[:500]})
            persist_extraction(session, document=document, ocr=ocr, extraction=extraction)
            document.status = "EXTRACTED"
            document.status = "VALIDATING"
            validation, result_validation = persist_validation(session, document=document, ocr=ocr, job=job, extraction=extraction)
            recommendation = result_validation.review_recommendation
            if recommendation and recommendation.required:
                if validation.review_task_id is None:
                    recommendation_metadata = result_validation.to_dict()["review_recommendation"]["metadata"]
                    review = create_review_task(
                        session, project_id=document.project_id, queue_type="DOCUMENT", target_type="LAND_RECORD",
                        target_id=document.id, severity=recommendation.severity, summary=recommendation.summary,
                        source_refs=list(recommendation.source_refs), blocking_issue_count=recommendation.blocking_issue_count,
                        metadata={**recommendation_metadata, "document_id": str(document.id), "validation_result_id": str(validation.id), "validation_version": validation.version},
                    )
                    validation.review_task_id = review.id
                    record_audit(session, "document.review_required", "document", document.id, project_id=document.project_id, metadata={"review_task_id": str(review.id), "validation_result_id": str(validation.id)})
                document.status = "REVIEW_REQUIRED"
            else:
                document.status = "VALIDATED"
                if validation.review_task_id is None:
                    validation_payload = result_validation.to_dict()
                    review = create_review_task(
                        session,
                        project_id=document.project_id,
                        queue_type="DOCUMENT",
                        target_type="LAND_RECORD",
                        target_id=document.id,
                        severity="INFO",
                        summary="Document verification review: validation passed",
                        source_refs=[],
                        blocking_issue_count=0,
                        metadata={
                            "document_id": str(document.id),
                            "validation_result_id": str(validation.id),
                            "validation_version": validation.version,
                            "confidence_summary": validation_payload.get("confidence_summary", {}),
                            "issue_codes": [],
                            "blocking_issue_codes": [],
                            "verification_only": True,
                        },
                    )
                    validation.review_task_id = review.id
                    record_audit(
                        session,
                        "document.verification_review_created",
                        "document",
                        document.id,
                        project_id=document.project_id,
                        metadata={"review_task_id": str(review.id), "validation_result_id": str(validation.id)},
                    )
                record_audit(session, "document.validated", "document", document.id, project_id=document.project_id, metadata={"validation_result_id": str(validation.id)})
            detail.output_refs_json = {"ocr_result_id": str(ocr.id), "validation_result_id": str(validation.id), "document_status": document.status}
            mark_job_completed(session, job)
            session.commit()
        except Exception:
            session.rollback()
            job = session.get(ProcessingJob, job_uuid)
            document = session.get(Document, detail.document_id) if detail else None
            if job is not None and job.status == "PROCESSING":
                if self.request.retries < self.max_retries:
                    mark_job_retry_queued(session, job, max((job.retry_count or 0) + 1, self.request.retries + 1))
                    if document is not None:
                        document.status = "QUEUED"
                        record_audit(session, "document.processing_retry_queued", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id), "retry_count": job.retry_count})
                else:
                    mark_job_failed(session, job, "Document AI processing failed")
                    if document is not None:
                        document.status = "FAILED"
                        record_audit(session, "document.processing_failed", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id)})
                session.commit()
            raise


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def revalidate_document(self, job_id: str) -> None:
    """Apply F.3 to persisted candidate evidence plus append-only corrections."""
    job_uuid = uuid.UUID(job_id)
    with SessionLocal() as session:
        job = session.get(ProcessingJob, job_uuid)
        detail = session.get(DocumentProcessingJob, job_uuid)
        if job is None or detail is None or detail.job_type != "DOCUMENT_REVALIDATE" or job.status != "QUEUED":
            return
        document = session.get(Document, detail.document_id)
        if document is None:
            return
        try:
            mark_job_processing(session, job)
            document.status = "VALIDATING"
            session.commit()
            ocr = session.scalar(
                select(DocumentOcrResultRecord)
                .where(DocumentOcrResultRecord.document_id == document.id)
                .order_by(DocumentOcrResultRecord.version.desc())
            )
            if ocr is None:
                raise RuntimeError("Persisted OCR result is unavailable.")
            extraction = extraction_from_records(session, document_id=document.id, ocr=ocr, include_corrections=True)
            validation, result_validation = persist_validation(session, document=document, ocr=ocr, job=job, extraction=extraction)
            recommendation = result_validation.review_recommendation
            if recommendation and recommendation.required:
                if validation.review_task_id is None:
                    recommendation_metadata = result_validation.to_dict()["review_recommendation"]["metadata"]
                    review = create_review_task(session, project_id=document.project_id, queue_type="DOCUMENT", target_type="LAND_RECORD", target_id=document.id, severity=recommendation.severity, summary=recommendation.summary, source_refs=list(recommendation.source_refs), blocking_issue_count=recommendation.blocking_issue_count, metadata={**recommendation_metadata, "document_id": str(document.id), "validation_result_id": str(validation.id), "validation_version": validation.version})
                    validation.review_task_id = review.id
                document.status = "REVIEW_REQUIRED"
            else:
                document.status = "VALIDATED"
                if validation.review_task_id is None:
                    validation_payload = result_validation.to_dict()
                    review = create_review_task(
                        session,
                        project_id=document.project_id,
                        queue_type="DOCUMENT",
                        target_type="LAND_RECORD",
                        target_id=document.id,
                        severity="INFO",
                        summary="Document verification review: validation passed",
                        source_refs=[],
                        blocking_issue_count=0,
                        metadata={
                            "document_id": str(document.id),
                            "validation_result_id": str(validation.id),
                            "validation_version": validation.version,
                            "confidence_summary": validation_payload.get("confidence_summary", {}),
                            "issue_codes": [],
                            "blocking_issue_codes": [],
                            "verification_only": True,
                        },
                    )
                    validation.review_task_id = review.id
                    record_audit(
                        session,
                        "document.verification_review_created",
                        "document",
                        document.id,
                        project_id=document.project_id,
                        metadata={"review_task_id": str(review.id), "validation_result_id": str(validation.id)},
                    )
            detail.output_refs_json = {"validation_result_id": str(validation.id), "document_status": document.status}
            mark_job_completed(session, job)
            session.commit()
        except Exception:
            session.rollback()
            job = session.get(ProcessingJob, job_uuid)
            if job is not None and job.status == "PROCESSING":
                document = session.get(Document, detail.document_id)
                if self.request.retries < self.max_retries:
                    mark_job_retry_queued(session, job, max((job.retry_count or 0) + 1, self.request.retries + 1))
                    if document is not None:
                        document.status = "VALIDATING"
                        record_audit(session, "document.revalidation_retry_queued", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id), "retry_count": job.retry_count})
                else:
                    mark_job_failed(session, job, "Document revalidation failed")
                    if document is not None:
                        document.status = "FAILED"
                        record_audit(session, "document.processing_failed", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id)})
                session.commit()
            raise


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_geopackage_import(self, job_id: str) -> None:
    """Validate and process a GeoPackage GIS import asynchronously."""
    job_uuid = uuid.UUID(job_id)
    with SessionLocal() as session:
        job = session.get(ProcessingJob, job_uuid)
        geoai_job = session.get(GeoAIJob, job_uuid)
        if job is None or job.status != "QUEUED":
            return
        try:
            job.retry_count = max(job.retry_count or 0, self.request.retries)
            mark_job_processing(session, job)
            record_audit(
                session,
                "geopackage.import_processing",
                "processing_job",
                job.id,
                project_id=job.project_id,
            )
            session.commit()
            from app.services.geopackage import process_geopackage_import_job

            process_geopackage_import_job(session, job_uuid)
        except Exception as error:
            session.rollback()
            job = session.get(ProcessingJob, job_uuid)
            geoai_job = session.get(GeoAIJob, job_uuid)
            if job is not None and job.status == "PROCESSING":
                from app.services.geopackage import GeoPackageServiceError

                if isinstance(error, GeoPackageServiceError) or self.request.retries >= self.max_retries:
                    mark_job_failed(session, job, str(error))
                    if isinstance(error, GeoPackageServiceError):
                        job.error_json = {"code": error.code, "message": str(error), "details": error.details}
                    if geoai_job is not None:
                        record_audit(
                            session,
                            "geopackage.import_failed",
                            "geoai_job",
                            geoai_job.id,
                            project_id=geoai_job.project_id,
                            metadata={"reason": str(error)},
                        )
                else:
                    mark_job_retry_queued(
                        session,
                        job,
                        max((job.retry_count or 0) + 1, self.request.retries + 1),
                    )
                    if geoai_job is not None:
                        record_audit(
                            session,
                            "geopackage.import_retry_queued",
                            "geoai_job",
                            geoai_job.id,
                            project_id=geoai_job.project_id,
                            metadata={"retry_count": job.retry_count},
                        )
                session.commit()
            if isinstance(error, GeoPackageServiceError):
                return
            raise
