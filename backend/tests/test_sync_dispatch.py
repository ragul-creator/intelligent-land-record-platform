"""Verify sync recovery permissions and commit-before-dispatch behavior."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.api.v1.sync import get_sync_operation, sync_batch
from app.core.errors import ApiError
from app.schemas.sync import SyncBatchRequest


def test_sync_dispatches_only_new_revalidation_jobs_after_commit():
    session = MagicMock()
    events = []
    session.commit.side_effect = lambda: events.append("commit")
    response = SimpleNamespace(results=[
        SimpleNamespace(status="APPLIED", result={"revalidation_job_id": "job-new"}),
        SimpleNamespace(status="DUPLICATE", result={"revalidation_job_id": "job-old"}),
    ])
    with patch("app.api.v1.sync.get_project_for_user"), \
         patch("app.api.v1.sync.apply_sync_batch", return_value=response), \
         patch("app.workers.tasks.revalidate_document.delay", side_effect=lambda job: events.append(job)):
        sync_batch(uuid.uuid4(), SyncBatchRequest(), session=session, user=MagicMock())
    assert events == ["commit", "job-new"]


def test_operation_recovery_requires_permission_for_its_stored_type():
    with patch("app.api.v1.sync.get_project_for_user"), \
         patch("app.api.v1.sync.get_sync_operation_by_id", return_value=SimpleNamespace(operation_type="FIELD_CORRECTION_CREATE")), \
         patch("app.api.v1.sync.user_permissions", return_value={"project:read"}):
        with pytest.raises(ApiError) as error:
            get_sync_operation(uuid.uuid4(), uuid.uuid4(), session=MagicMock(), user=MagicMock())
    assert error.value.status_code == 403
