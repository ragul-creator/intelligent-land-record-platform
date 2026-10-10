"""Metadata policy for the Phase B.2 private upload foundation."""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class FileCategory(StrEnum):
    DOCUMENT = "DOCUMENT"
    IMAGERY = "IMAGERY"
    GIS = "GIS"
    SUPPORTING = "SUPPORTING"
    GIS_IMPORT = "GIS_IMPORT"


def upload_permission_for_category(category: FileCategory | str) -> str:
    """Keep the upload permission decision consistent across presign and completion."""
    parsed = FileCategory(category)
    if parsed == FileCategory.IMAGERY:
        return "imagery:upload"
    if parsed == FileCategory.GIS_IMPORT:
        return "geo:edit_draft"
    return "document:upload"


ALLOWED_CONTENT_TYPES: dict[FileCategory, frozenset[str]] = {
    FileCategory.DOCUMENT: frozenset({"application/pdf", "image/jpeg", "image/png", "image/tiff"}),
    FileCategory.IMAGERY: frozenset({"image/tiff", "image/jpeg", "image/png", "application/geotiff"}),
    FileCategory.GIS: frozenset({"application/geo+json", "application/json", "application/zip"}),
    FileCategory.SUPPORTING: frozenset({"application/pdf", "image/jpeg", "image/png", "text/csv"}),
    FileCategory.GIS_IMPORT: frozenset({
        "application/geopackage+sqlite3",
        "application/x-sqlite3",
        "application/octet-stream",
    }),
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
        if self.category == FileCategory.GIS_IMPORT and not self.filename.lower().endswith(".gpkg"):
            raise ValueError("GIS_IMPORT files must have a .gpkg extension.")
        return self
