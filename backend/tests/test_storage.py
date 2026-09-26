import uuid

import pytest

from app.core.config import Settings
from app.core.storage import PrivateObjectStorage
from app.services.file_policy import FileCategory, UploadMetadata, upload_permission_for_category


class FakeS3Client:
    def __init__(self, presign_url: str = "https://signed.example") -> None:
        self.created_buckets: list[str] = []
        self.presign_calls: list[dict] = []
        self.presign_url = presign_url

    def head_bucket(self, **_kwargs) -> None:
        return None

    def create_bucket(self, **kwargs) -> None:
        self.created_buckets.append(kwargs["Bucket"])

    def head_object(self, **_kwargs):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def generate_presigned_url(self, operation, **kwargs) -> str:
        self.presign_calls.append({"operation": operation, **kwargs})
        return f"{self.presign_url}/{operation}"


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
    storage = PrivateObjectStorage(
        Settings(signed_url_expiry_seconds=900, s3_public_endpoint="http://minio:9000"),
        client=client,
    )

    upload_url, headers = storage.presign_upload("projects/a/originals/b/record.pdf", "application/pdf", "a" * 64)
    download_url = storage.presign_download("projects/a/originals/b/record.pdf")

    assert upload_url.startswith("https://signed.example/")
    assert download_url.startswith("https://signed.example/")
    assert headers == {"Content-Type": "application/pdf", "x-amz-meta-sha256": "a" * 64}
    assert [call["operation"] for call in client.presign_calls] == ["put_object", "get_object"]
    assert all(call["ExpiresIn"] == 900 for call in client.presign_calls)
    assert all("ACL" not in call["Params"] for call in client.presign_calls)


def test_browser_presigns_use_the_public_endpoint() -> None:
    internal_client = FakeS3Client()
    public_client = FakeS3Client("http://localhost:9001")
    storage = PrivateObjectStorage(
        Settings(
            s3_endpoint="http://minio:9000",
            s3_public_endpoint="http://localhost:9001",
        ),
        client=internal_client,
        public_client=public_client,
    )

    storage.ensure_private_bucket()
    upload_url, _ = storage.presign_upload("projects/a/originals/b/imagery.tif", "image/tiff", "a" * 64)

    assert upload_url.startswith("http://localhost:9001/")
    assert not internal_client.presign_calls
    assert [call["operation"] for call in public_client.presign_calls] == ["put_object"]


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


@pytest.mark.parametrize(
    "mime_type",
    [
        "application/geopackage+sqlite3",
        "application/x-sqlite3",
        "application/octet-stream",
    ],
)
def test_gis_import_metadata_accepts_supported_mime_types(mime_type: str) -> None:
    metadata = UploadMetadata(
        filename="cadastral_survey.gpkg",
        content_type=mime_type,
        size_bytes=4096,
        category=FileCategory.GIS_IMPORT,
    )
    assert metadata.category == FileCategory.GIS_IMPORT
    assert metadata.content_type == mime_type


def test_gis_import_metadata_rejects_non_gpkg_extensions() -> None:
    for invalid_filename in ("parcels.geojson", "parcels.zip", "parcels.sqlite", "parcels.gpkg.bak", "parcels.pdf"):
        with pytest.raises(ValueError, match=r"\.gpkg extension"):
            UploadMetadata(
                filename=invalid_filename,
                content_type="application/geopackage+sqlite3",
                size_bytes=4096,
                category=FileCategory.GIS_IMPORT,
            )


def test_gis_import_metadata_rejects_unsupported_mime_types() -> None:
    for invalid_mime in ("application/geo+json", "application/json", "application/pdf", "image/tiff"):
        with pytest.raises(ValueError, match="Content type is not allowed"):
            UploadMetadata(
                filename="parcels.gpkg",
                content_type=invalid_mime,
                size_bytes=4096,
                category=FileCategory.GIS_IMPORT,
            )


def test_upload_permission_for_category_enforces_rbac_rules() -> None:
    assert upload_permission_for_category(FileCategory.GIS_IMPORT) == "geo:edit_draft"
    assert upload_permission_for_category("GIS_IMPORT") == "geo:edit_draft"
    assert upload_permission_for_category(FileCategory.IMAGERY) == "imagery:upload"
    assert upload_permission_for_category(FileCategory.DOCUMENT) == "document:upload"
    assert upload_permission_for_category(FileCategory.GIS) == "document:upload"
    assert upload_permission_for_category(FileCategory.SUPPORTING) == "document:upload"


def test_existing_categories_and_gis_behavior_preserved() -> None:
    gis_meta = UploadMetadata(
        filename="boundary.geojson",
        content_type="application/geo+json",
        size_bytes=2048,
        category=FileCategory.GIS,
    )
    assert gis_meta.category == FileCategory.GIS

    doc_meta = UploadMetadata(
        filename="deed.pdf",
        content_type="application/pdf",
        size_bytes=2048,
        category=FileCategory.DOCUMENT,
    )
    assert doc_meta.category == FileCategory.DOCUMENT

    img_meta = UploadMetadata(
        filename="ortho.tif",
        content_type="image/tiff",
        size_bytes=2048,
        category=FileCategory.IMAGERY,
    )
    assert img_meta.category == FileCategory.IMAGERY
