"""Database-backed Celery tasks for registered files, GeoAI, and Document AI."""

import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.storage import get_storage_service
from app.audit.service import record_audit
from app.models import Building, Document, DocumentProcessingJob, DocumentOcrResultRecord, File, GeoAIJob, ImageryAsset, ProcessingJob, Road
from app.services.geoai import GeoAIServiceError, _to_wgs84, persist_parcel_import
from app.core.config import get_settings
from geoalchemy2.shape import from_shape
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
            geoai_job.output_refs_json = {"imagery_asset_id": str(asset.id), "building_ids": created_ids, "feature_count": len(created_ids), "model_version": result.features[0].model_version if result.features else None}
            job.progress = 100
            mark_job_completed(session, job)
            record_audit(session, "geoai.buildings_created", "geoai_job", geoai_job.id, project_id=geoai_job.project_id, metadata={"imagery_asset_id": str(asset.id), "feature_count": len(created_ids)})
            session.commit()
        except Exception as error:
            session.rollback()
            job, geoai_job = session.get(ProcessingJob, job_uuid), session.get(GeoAIJob, job_uuid)
            if job is not None and job.status == "PROCESSING":
                _fail_geoai_job(session, job, geoai_job, str(error))
                session.commit()
            return


@celery_app.task(bind=True)
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
            checkpoint = get_settings().geoai_road_checkpoint
            if not checkpoint or not Path(checkpoint).is_file():
                raise GeoAIServiceError("Road GeoAI is unavailable: GEOAI_ROAD_CHECKPOINT is not configured to a readable checkpoint.")
            asset = session.get(ImageryAsset, geoai_job.imagery_asset_id)
            source = session.get(File, asset.file_id) if asset else None
            if asset is None or source is None or asset.project_id != geoai_job.project_id:
                raise GeoAIServiceError("The requested imagery asset is unavailable in this project.")
            source_bytes = get_storage_service().read_private_object(source.storage_key)
            with tempfile.NamedTemporaryFile(suffix=Path(source.original_name).suffix or ".tif", delete=False) as temporary:
                temporary.write(source_bytes)
                source_path = Path(temporary.name)
            try:
                from ai.geoai.runtime.roads import infer_and_vectorize_geotiff

                result = infer_and_vectorize_geotiff(source_path, checkpoint=checkpoint, device=get_settings().geoai_device, threshold=get_settings().geoai_road_threshold)
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
            for feature in result.features:
                road = Road(
                    project_id=geoai_job.project_id,
                    geometry=from_shape(_to_wgs84(feature.geometry, result.source_crs), srid=4326),
                    road_class="ROAD",
                    source="AI_CANDIDATE",
                    source_reference=source_reference,
                    confidence=feature.confidence,
                    model_version=feature.model_version,
                    status="AI_PRELIMINARY",
                    verification_status="UNVERIFIED",
                    length_m=feature.length_m,
                    processed_at=datetime.fromisoformat(feature.processed_at.replace("Z", "+00:00")),
                )
                session.add(road)
                session.flush()
                created_ids.append(str(road.id))
            geoai_job.metrics_json = result.processing_parameters
            geoai_job.output_refs_json = {"imagery_asset_id": str(asset.id), "road_ids": created_ids, "feature_count": len(created_ids), "model_version": result.features[0].model_version if result.features else None}
            job.progress = 100
            mark_job_completed(session, job)
            record_audit(session, "geoai.roads_created", "geoai_job", geoai_job.id, project_id=geoai_job.project_id, metadata={"imagery_asset_id": str(asset.id), "feature_count": len(created_ids)})
            session.commit()
        except Exception as error:
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
                from ai.document_ai.extraction import extract_land_record_fields
                from ai.document_ai.pipeline import OcrPipeline

                result = OcrPipeline().process(source_path, source_id=str(document.id), languages=detail.requested_languages_json, allowed_root=source_path.parent)
            finally:
                source_path.unlink(missing_ok=True)

            ocr = persist_ocr_result(session, document=document, job=job, detail=detail, result=result)
            extraction = extract_land_record_fields(result)
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
