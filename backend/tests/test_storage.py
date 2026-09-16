import uuid

import pytest

from app.core.config import Settings
from app.core.storage import PrivateObjectStorage
from app.services.file_policy import FileCategory, UploadMetadata


class FakeS3Client:
    def __init__(self) -> None:
        self.created_buckets: list[str] = []
        self.presign_calls: list[dict] = []

    def head_bucket(self, **_kwargs) -> None:
        return None

    def create_bucket(self, **kwargs) -> None:
        self.created_buckets.append(kwargs["Bucket"])

    def head_object(self, **_kwargs):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def generate_presigned_url(self, operation, **kwargs) -> str:
        self.presign_calls.append({"operation": operation, **kwargs})
        return f"https://signed.example/{operation}"


def test_storage_key_is_server_generated_and_rejects_path_traversal() -> None:
    storage = PrivateObjectStorage(Settings(), client=FakeS3Client())
    project_id = uuid.uuid4()
    file_id = uuid.uuid4()

    key = storage.generate_storage_key(project_id, file_id, "record.pdf")

    assert key == f"projects/{project_id}/originals/{file_id}/record.pdf"
    with pytest.raises(ValueError):
        storage.generate_storage_key(project_id, file_id, "../record.pdf")


def test_presigned_urls_are_private_and_expire() -> None:
    client = FakeS3Client()
    storage = PrivateObjectStorage(Settings(signed_url_expiry_seconds=900), client=client)

    upload_url, headers = storage.presign_upload("projects/a/originals/b/record.pdf", "application/pdf", "a" * 64)
    download_url = storage.presign_download("projects/a/originals/b/record.pdf")

    assert upload_url.startswith("https://signed.example/")
    assert download_url.startswith("https://signed.example/")
    assert headers == {"Content-Type": "application/pdf", "x-amz-meta-sha256": "a" * 64}
    assert [call["operation"] for call in client.presign_calls] == ["put_object", "get_object"]
    assert all(call["ExpiresIn"] == 900 for call in client.presign_calls)
    assert all("ACL" not in call["Params"] for call in client.presign_calls)


def test_upload_metadata_rejects_unsafe_or_disallowed_values() -> None:
    metadata = UploadMetadata(
        filename="record.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        category=FileCategory.DOCUMENT,
    )
    assert metadata.content_type == "application/pdf"

    with pytest.raises(ValueError):
        UploadMetadata(
            filename="../record.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            category=FileCategory.DOCUMENT,
        )
    with pytest.raises(ValueError):
        UploadMetadata(
            filename="record.pdf",
            content_type="application/zip",
            size_bytes=1024,
            category=FileCategory.DOCUMENT,
        )
