import uuid
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.foundation import GeoAIJob, ProcessingJob, TimestampedModel

GIS_IMPORT_STATUSES = ("QUEUED", "PROCESSING", "COMPLETED", "FAILED", "RETRY_QUEUED")


class GisImportRun(TimestampedModel, Base):
    """Tracks a single GeoPackage import execution, status, and bounded summaries."""

    __tablename__ = "gis_import_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'RETRY_QUEUED')",
            name="ck_gis_import_runs_status",
        ),
        UniqueConstraint("processing_job_id", name="uq_gis_import_runs_processing_job_id"),
        Index("ix_gis_import_runs_project_created", "project_id", "created_at"),
        Index("ix_gis_import_runs_file_id", "file_id"),
        Index("ix_gis_import_runs_status", "status"),
        Index("ix_gis_import_runs_requested_by", "requested_by_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("files.id", ondelete="RESTRICT"), nullable=False
    )
    processing_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    layer_mapping_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), server_default="QUEUED", nullable=False
    )
    detected_layers_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False
    )


def sync_gis_import_run(session: Session, import_run: GisImportRun) -> GisImportRun:
    """Synchronize GisImportRun status, summary, and error metrics from the underlying ProcessingJob."""
    job = session.get(ProcessingJob, import_run.processing_job_id)
    if job is None:
        return import_run

    updated = False
    if job.status == "PROCESSING" and import_run.status != "PROCESSING":
        import_run.status = "PROCESSING"
        updated = True
    elif job.status == "COMPLETED" and import_run.status != "COMPLETED":
        import_run.status = "COMPLETED"
        geoai_job = session.get(GeoAIJob, import_run.processing_job_id)
        if geoai_job is not None:
            output = geoai_job.output_refs_json or {}
            layers = output.get("layers", {})
            summary = {
                "layers_detected": len(layers),
                "layers_mapped": len(layers),
                "features_read": sum(l.get("total", 0) for l in layers.values()),
                "features_imported": sum(l.get("valid", 0) for l in layers.values()),
                "features_rejected": sum(l.get("rejected", 0) for l in layers.values()),
                "repairs_applied": sum(l.get("repaired", 0) for l in layers.values()),
                "warnings": output.get("warnings", []),
                "rejection_summary": output.get("rejection_summary", []),
                "layer_details": layers,
            }
            detected = [
                {
                    "logical_layer": k,
                    "source_layer": v.get("source_layer"),
                    "crs": v.get("source_crs"),
                }
                for k, v in layers.items()
            ]
            import_run.summary_json = summary
            import_run.detected_layers_json = {"layers": detected}
        updated = True
    elif job.status == "FAILED" and import_run.status != "FAILED":
        import_run.status = "FAILED"
        err = job.error_json or {}
        import_run.error_code = err.get("code") or "GIS_IMPORT_FAILED"
        updated = True
    elif job.status == "RETRY_QUEUED" and import_run.status != "RETRY_QUEUED":
        import_run.status = "RETRY_QUEUED"
        updated = True

    if updated:
        session.commit()

    return import_run
