"""Idempotent synthetic Tamil Nadu demo dataset for Phase H.2.

All identifiers, geometries, OCR text, names, and workflow records created here are
synthetic demonstration data. They are not official cadastral or ownership records.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from geoalchemy2.shape import from_shape
from shapely.geometry import LineString, Polygon
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import hash_password
from app.models import (
    Building,
    Document,
    DocumentExtractedField,
    DocumentOcrResultRecord,
    DocumentProcessingJob,
    DocumentValidationResultRecord,
    File,
    GeoAIJob,
    ImageryAsset,
    LandUseFeature,
    Parcel,
    ParcelGeometryVersion,
    ProcessingJob,
    Project,
    ProjectMember,
    RecordParcelLink,
    Road,
    Role,
    TopologyError,
    User,
    UserRole,
)
from app.services.review import create_review_task
from app.services.user_identities import generate_login_id


DEMO_PROJECT_NAME = "Tamil Nadu Integrated Land Records Demo"
DEMO_SOURCE_REFERENCE = "H2-SYNTHETIC-TN-DEMO"
DEMO_USERS = {
    "ADMIN": ("admin.demo.tn@example.invalid", "Demo Administrator"),
    "OFFICER": ("officer.demo.tn@example.invalid", "Demo Land Records Officer"),
    "REVIEWER": ("reviewer.demo.tn@example.invalid", "Demo Review Officer"),
    "SURVEYOR": ("surveyor.demo.tn@example.invalid", "Demo Surveyor"),
    "VIEWER": ("viewer.demo.tn@example.invalid", "Demo Viewer"),
}


def _user(session: Session, role_name: str, password: str) -> User:
    email, full_name = DEMO_USERS[role_name]
    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return existing

    role = session.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        raise RuntimeError(f"{role_name} role is missing; apply migrations before seeding the demo.")

    user = User(
        login_id=generate_login_id(session, role_name),
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        is_active=True,
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _parcel(
    session: Session,
    project: Project,
    identifier: str,
    polygon: Polygon,
    area_m2: float,
    *,
    source_reference: str,
) -> Parcel:
    parcel = Parcel(
        project_id=project.id,
        external_identifier=identifier,
        source="CADASTRAL_GIS",
        source_reference=source_reference,
        status="DRAFT",
        verification_status="UNVERIFIED",
        current_geometry_version=1,
        coordinate_space="WORLD",
        source_crs="EPSG:4326",
        confidence=None,
        model_version=None,
        ai_boundary_status="NOT_DETERMINED",
        requires_survey=True,
    )
    session.add(parcel)
    session.flush()
    session.add(
        ParcelGeometryVersion(
            parcel_id=parcel.id,
            version=1,
            geometry=from_shape(polygon, srid=4326),
            source_geometry_json={
                "type": "Feature",
                "properties": {
                    "district": "Chennai",
                    "village": "Synthetic Demo Village",
                    "demo_notice": "Synthetic hackathon data; not an official cadastral record.",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[list(point) for point in polygon.exterior.coords]],
                },
            },
            source="CADASTRAL_GIS",
            source_reference=source_reference,
            coordinate_space="WORLD",
            source_crs="EPSG:4326",
            area_m2=area_m2,
            area_sqft=area_m2 * 10.7639104167,
            validation_status="VALID",
            created_by_type="IMPORT",
            processed_at=datetime.now(UTC),
        )
    )
    return parcel


def _document(
    session: Session,
    project: Project,
    officer: User,
    *,
    filename: str,
    status: str,
    survey_number: str,
    confidence: float,
    version_tag: str,
) -> tuple[Document, DocumentValidationResultRecord]:
    file = File(
        project_id=project.id,
        original_name=filename,
        category="DOCUMENT",
        mime_type="application/pdf",
        size_bytes=2048,
        storage_key=f"projects/{project.id}/demo/{uuid.uuid4()}/{filename}",
        status="UPLOADED",
    )
    session.add(file)
    session.flush()

    document = Document(
        project_id=project.id,
        file_id=file.id,
        uploaded_by_user_id=officer.id,
        status=status,
    )
    session.add(document)
    session.flush()

    job = ProcessingJob(
        project_id=project.id,
        job_type="DOCUMENT_AI_PROCESS",
        idempotency_key=f"h2-demo-document:{document.id}",
        status="COMPLETED",
        progress=100,
        retry_count=0,
    )
    session.add(job)
    session.flush()
    detail = DocumentProcessingJob(
        id=job.id,
        document_id=document.id,
        requested_languages_json=["tam", "eng"],
        processing_version=1,
        job_type="DOCUMENT_AI_PROCESS",
        requested_by_user_id=officer.id,
        output_refs_json={"document_status": status, "demo": True},
    )
    session.add(detail)

    processed_at = datetime.now(UTC)
    ocr = DocumentOcrResultRecord(
        document_id=document.id,
        processing_job_id=job.id,
        version=1,
        status="OCR_PRELIMINARY",
        requested_languages_json=["tam", "eng"],
        project_tested_languages_json=["tam", "eng"],
        engine="tesseract",
        engine_version="demo",
        model_version="h2-synthetic-demo",
        page_count=1,
        confidence=confidence,
        payload_json={
            "source_id": str(document.id),
            "demo_notice": "Synthetic OCR payload for hackathon demonstration only.",
            "pages": [
                {
                    "page_number": 1,
                    "text": (
                        f"தமிழ்நாடு / TAMIL NADU — SYNTHETIC DEMO\n"
                        f"Survey No / சர்வே எண்: {survey_number}\n"
                        "Village / கிராமம்: Synthetic Demo Village\n"
                        "District / மாவட்டம்: Chennai\n"
                        "Area / பரப்பளவு: 111.5 sq m"
                    ),
                    "confidence": confidence,
                }
            ],
        },
        processed_at=processed_at,
    )
    session.add(ocr)
    session.flush()

    field_values = (
        ("survey_number", survey_number, survey_number, confidence),
        ("village", "Synthetic Demo Village", "Synthetic Demo Village", max(confidence - 0.02, 0.0)),
        ("district", "Chennai", "Chennai", max(confidence - 0.01, 0.0)),
        ("plot_area", "111.5 sq m", {"value": 111.5, "unit": "m2"}, max(confidence - 0.03, 0.0)),
        ("owner_details", "Demo Citizen — synthetic", "Demo Citizen — synthetic", max(confidence - 0.04, 0.0)),
    )
    for index, (name, original, normalized, field_confidence) in enumerate(field_values):
        session.add(
            DocumentExtractedField(
                document_id=document.id,
                ocr_result_id=ocr.id,
                candidate_index=index,
                field_name=name,
                original_value=original,
                normalized_value_json=normalized,
                confidence=field_confidence,
                page_number=1,
                bounding_box_json={"left": 40, "top": 80 + index * 36, "width": 260, "height": 24},
                source_id=str(document.id),
                model_version="h2-synthetic-demo",
                extractor_version="document-extraction-mvp-v1",
                processed_at=processed_at,
            )
        )

    validation_status = "VALID" if status == "VALIDATED" else "REVIEW_REQUIRED"
    issues = [] if status == "VALIDATED" else [
        {
            "code": "LOW_CONFIDENCE_EVIDENCE",
            "severity": "MEDIUM",
            "message": "Synthetic demo record contains low-confidence evidence and requires human review.",
        }
    ]
    validation = DocumentValidationResultRecord(
        document_id=document.id,
        ocr_result_id=ocr.id,
        processing_job_id=job.id,
        version=1,
        status=validation_status,
        validation_version="document-validation-mvp-v1",
        report_json={"issues": issues, "demo_notice": "Synthetic validation result."},
        confidence_summary_json={"overall": confidence, "source": "synthetic_demo"},
        checks_json={
            "cross_database_verification": {"status": "NOT_PERFORMED"},
            "duplicate_detection": {"status": "NOT_PERFORMED"},
        },
        processed_at=processed_at,
    )
    session.add(validation)
    session.flush()

    if status == "REVIEW_REQUIRED":
        review = create_review_task(
            session,
            project_id=project.id,
            queue_type="DOCUMENT",
            target_type="LAND_RECORD",
            target_id=document.id,
            severity="MEDIUM",
            summary="Synthetic Tamil Nadu demo record requires human verification.",
            source_refs=[f"document:{document.id}:page:1"],
            metadata={
                "document_id": str(document.id),
                "validation_result_id": str(validation.id),
                "validation_version": validation.version,
                "demo": True,
            },
            blocking_issue_count=0,
            created_by_user_id=officer.id,
        )
        validation.review_task_id = review.id

    return document, validation


def seed_tamil_nadu_demo(session: Session, password: str) -> dict[str, object]:
    """Create one idempotent, clearly synthetic end-to-end Tamil Nadu demo project."""
    if len(password) < 12:
        raise ValueError("Demo password must be at least 12 characters.")

    users = {role: _user(session, role, password) for role in DEMO_USERS}

    project = session.scalar(select(Project).where(Project.name == DEMO_PROJECT_NAME))
    if project is not None:
        session.commit()
        return {
            "created": False,
            "project_id": project.id,
            "users": {role: user.login_id for role, user in users.items()},
        }

    project = Project(
        name=DEMO_PROJECT_NAME,
        description=(
            "Synthetic Chennai-area hackathon dataset demonstrating Tamil + English Document AI, "
            "human review, cadastral Web-GIS, and auditable record ↔ parcel association. "
            "No row in this project is an official or statutory land record."
        ),
        state="ACTIVE",
        owner_id=users["ADMIN"].id,
    )
    session.add(project)
    session.flush()
    for role, user in users.items():
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))

    imagery = ImageryAsset(
        project_id=project.id,
        source_reference=f"{DEMO_SOURCE_REFERENCE}:ORTHOMOSAIC",
        source_crs="EPSG:4326",
        coordinate_space="WORLD",
        metadata_json={
            "demo": True,
            "district": "Chennai",
            "notice": "Synthetic imagery metadata only; not survey evidence.",
        },
    )
    session.add(imagery)

    parcel_a = _parcel(
        session,
        project,
        "TN-DEMO-101/4",
        Polygon([
            (80.24000, 13.06000),
            (80.24013, 13.06000),
            (80.24013, 13.06009),
            (80.24000, 13.06009),
            (80.24000, 13.06000),
        ]),
        111.5,
        source_reference=f"{DEMO_SOURCE_REFERENCE}:CADASTRAL:A",
    )
    parcel_b = _parcel(
        session,
        project,
        "TN-DEMO-101/5",
        Polygon([
            (80.24014, 13.06000),
            (80.24027, 13.06000),
            (80.24027, 13.06009),
            (80.24014, 13.06009),
            (80.24014, 13.06000),
        ]),
        111.1,
        source_reference=f"{DEMO_SOURCE_REFERENCE}:CADASTRAL:B",
    )

    building_polygon = Polygon([
        (80.240025, 13.060020),
        (80.240080, 13.060020),
        (80.240080, 13.060062),
        (80.240025, 13.060062),
        (80.240025, 13.060020),
    ])
    session.add(
        Building(
            project_id=project.id,
            geometry=from_shape(building_polygon, srid=4326),
            source="AI_CANDIDATE",
            source_reference=f"{DEMO_SOURCE_REFERENCE}:BUILDING",
            confidence=0.93,
            model_version="deeplabv3-resnet50-demo",
            status="AI_PRELIMINARY",
            verification_status="UNVERIFIED",
            area_m2=25.4,
            area_sqft=273.4,
            processed_at=datetime.now(UTC),
        )
    )
    session.add(
        Road(
            project_id=project.id,
            geometry=from_shape(LineString([(80.23994, 13.05997), (80.24031, 13.05997)]), srid=4326),
            road_class="PATHWAY",
            source="EXISTING_GIS",
            source_reference=f"{DEMO_SOURCE_REFERENCE}:PATHWAY",
            confidence=None,
            model_version=None,
            status="DRAFT",
            verification_status="UNVERIFIED",
            length_m=40.1,
            processed_at=datetime.now(UTC),
        )
    )
    session.add(
        LandUseFeature(
            project_id=project.id,
            geometry=from_shape(Polygon([
                (80.23998, 13.05998),
                (80.24029, 13.05998),
                (80.24029, 13.06011),
                (80.23998, 13.06011),
                (80.23998, 13.05998),
            ]), srid=4326),
            land_use_class="RESIDENTIAL",
            source="MANUAL_DRAWN",
            source_reference=f"{DEMO_SOURCE_REFERENCE}:LAND_USE",
            confidence=None,
            model_version=None,
            status="DRAFT",
            verification_status="UNVERIFIED",
            area_m2=365.0,
            area_sqft=3928.8,
            processed_at=datetime.now(UTC),
        )
    )
    session.add(
        TopologyError(
            project_id=project.id,
            parcel_id=parcel_b.id,
            code="DEMO_REVIEW_MARKER",
            severity="REVIEW",
            area_m2=None,
            message="Synthetic demo topology marker for human-review workflow.",
            resolved=False,
        )
    )

    geo_job = ProcessingJob(
        project_id=project.id,
        job_type="PARCEL_IMPORT",
        idempotency_key=f"h2-demo-geo:{project.id}",
        status="COMPLETED",
        progress=100,
        retry_count=0,
    )
    session.add(geo_job)
    session.flush()
    session.add(
        GeoAIJob(
            id=geo_job.id,
            project_id=project.id,
            imagery_asset_id=imagery.id,
            requested_by_user_id=users["SURVEYOR"].id,
            job_type="PARCEL_IMPORT",
            parameters_json={"demo": True, "source_reference": DEMO_SOURCE_REFERENCE},
            metrics_json={"demo": True},
            output_refs_json={"parcel_id": str(parcel_a.id), "geometry_version": 1},
        )
    )

    validated_document, validated_result = _document(
        session,
        project,
        users["OFFICER"],
        filename="tn_demo_validated_record.pdf",
        status="VALIDATED",
        survey_number="TN-DEMO-101/4",
        confidence=0.94,
        version_tag="validated",
    )
    _document(
        session,
        project,
        users["OFFICER"],
        filename="tn_demo_review_record.pdf",
        status="REVIEW_REQUIRED",
        survey_number="TN-DEMO-101/5",
        confidence=0.58,
        version_tag="review",
    )

    link = RecordParcelLink(
        project_id=project.id,
        document_id=validated_document.id,
        document_validation_result_id=validated_result.id,
        parcel_id=parcel_a.id,
        link_status="CONFIRMED",
        link_method="EXACT_SURVEY_IDENTIFIER",
        confidence=0.97,
        rationale_json={
            "policy_version": "h2-synthetic-demo",
            "match_factors": {"exact_synthetic_identifier": True, "district": True, "area_support": True},
            "demo_notice": "Synthetic workflow association; not ownership proof.",
        },
        provenance_json={
            "document_validation_result_id": str(validated_result.id),
            "parcel": {
                "external_identifier": parcel_a.external_identifier,
                "source": parcel_a.source,
                "source_reference": parcel_a.source_reference,
            },
            "demo": True,
        },
        created_by_user_id=users["OFFICER"].id,
        reviewed_by_user_id=users["REVIEWER"].id,
        reviewed_at=datetime.now(UTC),
        review_reason="Synthetic H.2 demo association.",
    )
    session.add(link)
    session.flush()

    record_audit(
        session,
        "demo.seeded",
        "project",
        project.id,
        actor_id=users["ADMIN"].id,
        project_id=project.id,
        metadata={
            "dataset": "H2 synthetic Tamil Nadu demo",
            "statutory": False,
            "document_count": 2,
            "parcel_count": 2,
        },
    )
    session.commit()

    return {
        "created": True,
        "project_id": project.id,
        "users": {role: user.login_id for role, user in users.items()},
    }
