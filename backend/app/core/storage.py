"""Private S3-compatible object storage adapter."""

import re
import uuid
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from app.core.config import Settings, get_settings

_UNSAFE_FILENAME = re.compile(r"[\\/\x00]")


class StorageObjectNotFoundError(Exception):
    """Raised when a requested private object is absent."""


class StorageObjectAlreadyExistsError(Exception):
    """Raised to prevent accidental overwrite of immutable originals."""


@dataclass(frozen=True)
class ObjectInfo:
    """S3 object metadata needed to complete an upload registration."""

    size_bytes: int
    content_type: str
    metadata: dict[str, str]


class PrivateObjectStorage:
    """Provider-neutral boundary for private S3-compatible object storage."""

    def __init__(self, settings: Settings, client: BaseClient | None = None) -> None:
        self.bucket = settings.s3_bucket
        self.expiry_seconds = settings.signed_url_expiry_seconds
        self.client = client or boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    def ensure_private_bucket(self) -> None:
        """Create the bucket when absent; S3/MinIO buckets are private by default."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def generate_storage_key(self, project_id: uuid.UUID, file_id: uuid.UUID, filename: str) -> str:
        """Generate a server-controlled, immutable original-object key."""
        if not filename or _UNSAFE_FILENAME.search(filename) or PurePath(filename).name != filename:
            raise ValueError("Filename contains an unsafe path component.")
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
        return f"projects/{project_id}/originals/{file_id}/{safe_name}"

    def presign_upload(
        self,
        storage_key: str,
        content_type: str,
        checksum: str | None,
    ) -> tuple[str, dict[str, str]]:
        """Return a bounded PUT URL and required headers without exposing credentials."""
        self.ensure_private_bucket()
        try:
            self.client.head_object(Bucket=self.bucket, Key=storage_key)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code not in {"404", "NoSuchKey", "NotFound"}:
                raise
        else:
            raise StorageObjectAlreadyExistsError(storage_key)

        metadata = {"sha256": checksum} if checksum else {}
        url = self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": storage_key,
                "ContentType": content_type,
                "Metadata": metadata,
            },
            ExpiresIn=self.expiry_seconds,
            HttpMethod="PUT",
        )
        headers = {"Content-Type": content_type}
        if checksum:
            headers["x-amz-meta-sha256"] = checksum
        return url, headers

    def presign_download(self, storage_key: str) -> str:
        """Return a short-lived GET URL for a private stored object."""
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=self.expiry_seconds,
            HttpMethod="GET",
        )

    def get_object_info(self, storage_key: str) -> ObjectInfo:
        """Read object metadata without downloading the object body."""
        try:
            response: dict[str, Any] = self.client.head_object(Bucket=self.bucket, Key=storage_key)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise StorageObjectNotFoundError(storage_key) from error
            raise
        return ObjectInfo(
            size_bytes=int(response["ContentLength"]),
            content_type=str(response.get("ContentType", "")),
            metadata={str(key).lower(): str(value) for key, value in response.get("Metadata", {}).items()},
        )


def get_storage_service() -> PrivateObjectStorage:
    """FastAPI dependency factory for the configured S3-compatible provider."""
    return PrivateObjectStorage(get_settings())
