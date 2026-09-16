from app.core.permissions import APPLICATION_ROLES, PERMISSION_CODES
from app.models import foundation


def test_foundational_and_geoai_tables_are_registered() -> None:
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
