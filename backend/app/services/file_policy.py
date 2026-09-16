"""Metadata policy for the Phase B.2 private upload foundation."""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class FileCategory(StrEnum):
    DOCUMENT = "DOCUMENT"
    IMAGERY = "IMAGERY"
    GIS = "GIS"
    SUPPORTING = "SUPPORTING"


ALLOWED_CONTENT_TYPES: dict[FileCategory, frozenset[str]] = {
    FileCategory.DOCUMENT: frozenset({"application/pdf", "image/jpeg", "image/png", "image/tiff"}),
    FileCategory.IMAGERY: frozenset({"image/tiff", "image/jpeg", "image/png", "application/geotiff"}),
    FileCategory.GIS: frozenset({"application/geo+json", "application/json", "application/zip"}),
    FileCategory.SUPPORTING: frozenset({"application/pdf", "image/jpeg", "image/png", "text/csv"}),
}
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024 * 1024


class UploadMetadata(BaseModel):
    """Validated client metadata; no client-provided object key is accepted."""

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=MAX_UPLOAD_SIZE_BYTES)
    category: FileCategory
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value.strip() != value or any(character in value for character in ("/", "\\", "\x00")):
            raise ValueError("Filename contains an unsafe path component.")
        return value

    @field_validator("content_type")
    @classmethod
    def normalize_content_type(cls, value: str) -> str:
        return value.lower().strip()

    @model_validator(mode="after")
    def validate_allowed_content_type(self) -> "UploadMetadata":
        if self.content_type not in ALLOWED_CONTENT_TYPES[self.category]:
            raise ValueError("Content type is not allowed for the selected file category.")
        return self
