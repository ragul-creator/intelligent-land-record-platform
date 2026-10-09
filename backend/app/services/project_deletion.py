"""Transactional permanent deletion of one project and its project-scoped data."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


_DELETE_STATEMENTS = (
    "DELETE FROM gis_import_runs WHERE project_id = :project_id",
    "DELETE FROM record_parcel_links WHERE project_id = :project_id",
    "DELETE FROM document_field_corrections WHERE document_id IN (SELECT id FROM documents WHERE project_id = :project_id)",
    "DELETE FROM document_extracted_fields WHERE document_id IN (SELECT id FROM documents WHERE project_id = :project_id)",
    "DELETE FROM document_validation_results WHERE document_id IN (SELECT id FROM documents WHERE project_id = :project_id)",
    "DELETE FROM document_ocr_results WHERE document_id IN (SELECT id FROM documents WHERE project_id = :project_id)",
    "DELETE FROM document_processing_jobs WHERE document_id IN (SELECT id FROM documents WHERE project_id = :project_id)",
    "DELETE FROM parcel_geometry_versions WHERE parcel_id IN (SELECT id FROM parcels WHERE project_id = :project_id)",
    "DELETE FROM topology_errors WHERE project_id = :project_id",
    "DELETE FROM review_tasks WHERE project_id = :project_id",
    "DELETE FROM geoai_jobs WHERE project_id = :project_id",
    "DELETE FROM buildings WHERE project_id = :project_id",
    "DELETE FROM roads WHERE project_id = :project_id",
    "DELETE FROM land_use_features WHERE project_id = :project_id",
    "DELETE FROM parcels WHERE project_id = :project_id",
    "DELETE FROM imagery_assets WHERE project_id = :project_id",
    "DELETE FROM documents WHERE project_id = :project_id",
    "DELETE FROM processing_jobs WHERE project_id = :project_id",
    "DELETE FROM files WHERE project_id = :project_id",
    "DELETE FROM projects WHERE id = :project_id",
)


def project_storage_keys(session: Session, project_id: uuid.UUID) -> tuple[str, ...]:
    """Return source and derived object keys owned by a project before DB deletion."""

    keys = {
        value
        for value in session.scalars(
            text("SELECT storage_key FROM files WHERE project_id = :project_id"),
            {"project_id": project_id},
        )
        if value
    }
    preview_keys = session.scalars(
        text(
            """
            SELECT metadata_json ->> 'preview_storage_key'
            FROM imagery_assets
            WHERE project_id = :project_id
              AND metadata_json ? 'preview_storage_key'
            """
        ),
        {"project_id": project_id},
    )
    keys.update(value for value in preview_keys if value)
    return tuple(sorted(keys))


def delete_project_data(session: Session, project_id: uuid.UUID) -> None:
    """Delete project-scoped relational data in FK-safe order.

    Audit records are intentionally retained by the database. Their project_id is
    cleared by the projects -> audit_logs ON DELETE SET NULL relationship.
    """

    params = {"project_id": project_id}
    for statement in _DELETE_STATEMENTS:
        session.execute(text(statement), params)
