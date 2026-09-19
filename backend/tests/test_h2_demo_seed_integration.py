"""Phase H.2 synthetic Tamil Nadu demo seed integration checks."""

import os

import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.demo_seed import DEMO_PROJECT_NAME, seed_tamil_nadu_demo
from app.models import (
    Document,
    File,
    ImageryAsset,
    Parcel,
    Project,
    RecordParcelLink,
    ReviewTask,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run H.2 demo seed tests.",
)


def test_h2_demo_seed_is_idempotent_and_populates_vertical_slice() -> None:
    with SessionLocal() as session:
        first = seed_tamil_nadu_demo(session, "h2-local-demo-password")
    with SessionLocal() as session:
        second = seed_tamil_nadu_demo(session, "h2-local-demo-password")

    assert first["project_id"] == second["project_id"]
    assert set(second["users"]) == {"ADMIN", "OFFICER", "REVIEWER", "SURVEYOR", "VIEWER"}

    with SessionLocal() as session:
        project = session.scalar(select(Project).where(Project.name == DEMO_PROJECT_NAME))
        assert project is not None
        assert project.id == second["project_id"]
        seeded_documents = session.execute(
            select(File.original_name, Document.status)
            .join(Document, Document.file_id == File.id)
            .where(
                Document.project_id == project.id,
                File.storage_key.like(f"projects/{project.id}/demo/%"),
                File.original_name.in_(
                    ("tn_demo_validated_record.pdf", "tn_demo_review_record.pdf")
                ),
            )
        ).all()
        assert sorted(name for name, _ in seeded_documents) == [
            "tn_demo_review_record.pdf",
            "tn_demo_validated_record.pdf",
        ]
        assert session.scalar(select(func.count(Parcel.id)).where(Parcel.project_id == project.id)) == 2
        assert session.scalar(
            select(func.count(ImageryAsset.id)).where(
                ImageryAsset.project_id == project.id,
                ImageryAsset.file_id.is_(None),
            )
        ) == 1
        assert session.scalar(
            select(func.count(RecordParcelLink.id)).where(
                RecordParcelLink.project_id == project.id,
                RecordParcelLink.link_status == "CONFIRMED",
            )
        ) == 1
        assert session.scalar(
            select(func.count(ReviewTask.id)).where(
                ReviewTask.project_id == project.id,
                ReviewTask.status == "OPEN",
            )
        ) >= 1

        assert {status for _, status in seeded_documents} == {"VALIDATED", "REVIEW_REQUIRED"}
