from app.core.permissions import APPLICATION_ROLES, PERMISSION_CODES
from app.models import foundation
from app.models.documents import (
    Document,
    DocumentExtractedField,
    DocumentFieldCorrection,
    DocumentOcrResultRecord,
    DocumentProcessingJob,
    DocumentValidationResultRecord,
)


def test_foundational_geoai_and_document_ai_tables_are_registered() -> None:
    assert set(foundation.Base.metadata.tables) == {
        "users",
        "roles",
        "permissions",
        "user_roles",
        "role_permissions",
        "auth_sessions",
        "projects",
        "project_members",
        "files",
        "processing_jobs",
        "imagery_assets",
        "geoai_jobs",
        "parcels",
        "parcel_geometry_versions",
        "buildings",
        "roads",
        "land_use_features",
        "topology_errors",
        "audit_logs",
        "review_tasks",
        "documents",
        "document_processing_jobs",
        "document_ocr_results",
        "document_extracted_fields",
        "document_validation_results",
        "document_field_corrections",
    }


def test_role_and_permission_constants_match_approved_baseline() -> None:
    assert APPLICATION_ROLES == ("ADMIN", "OFFICER", "REVIEWER", "SURVEYOR", "VIEWER")
    assert "project:member_manage" in PERMISSION_CODES
    assert "system:admin" in PERMISSION_CODES
    assert len(PERMISSION_CODES) == 28


def test_foundational_constraints_are_declared() -> None:
    file_constraints = {constraint.name for constraint in foundation.File.__table__.constraints}
    job_constraints = {constraint.name for constraint in foundation.ProcessingJob.__table__.constraints}
    project_constraints = {constraint.name for constraint in foundation.Project.__table__.constraints}

    assert {"ck_files_size_bytes_nonnegative", "ck_files_sha256_length", "ck_files_category"} <= file_constraints
    assert {"ck_processing_jobs_progress", "ck_processing_jobs_retry_count", "ck_processing_jobs_status"} <= job_constraints
    assert "ck_projects_state" in project_constraints
    parcel_version_constraints = {constraint.name for constraint in foundation.ParcelGeometryVersion.__table__.constraints}
    assert "uq_parcel_geometry_versions_parcel_version" in parcel_version_constraints


def test_document_ai_version_and_referential_constraints_are_declared() -> None:
    document_constraints = {constraint.name for constraint in Document.__table__.constraints}
    ocr_constraints = {constraint.name for constraint in DocumentOcrResultRecord.__table__.constraints}
    field_constraints = {constraint.name for constraint in DocumentExtractedField.__table__.constraints}
    validation_constraints = {constraint.name for constraint in DocumentValidationResultRecord.__table__.constraints}
    correction_constraints = {constraint.name for constraint in DocumentFieldCorrection.__table__.constraints}

    assert "ck_documents_status" in document_constraints
    assert "uq_document_ocr_results_document_version" in ocr_constraints
    assert "uq_document_ocr_results_processing_job" in ocr_constraints
    assert "uq_document_extracted_fields_result_index" in field_constraints
    assert {"uq_document_validation_results_document_version", "uq_document_validation_results_processing_job"} <= validation_constraints
    assert "uq_document_field_corrections_field_version" in correction_constraints
    assert DocumentProcessingJob.__table__.foreign_keys
