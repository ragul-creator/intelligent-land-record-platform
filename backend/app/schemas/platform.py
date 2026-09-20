"""Contracts for H.2B.4 project search and export discovery."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


SearchResultKind = Literal["DOCUMENT", "PARCEL", "FIELD"]


class ProjectSearchItem(BaseModel):
    kind: SearchResultKind
    id: uuid.UUID
    evidence_id: uuid.UUID | None = None
    title: str
    subtitle: str | None = None
    status: str | None = None
    matched_value: str
    preliminary: bool = False


class ProjectSearchResponse(BaseModel):
    query: str
    items: list[ProjectSearchItem]
    total: int


class ExportDescriptor(BaseModel):
    code: Literal["RECORDS_CSV", "PARCELS_GEOJSON"]
    label: str
    path: str
    media_type: str
    description: str


class ExportManifestResponse(BaseModel):
    project_id: uuid.UUID
    disclaimer: str = Field(
        default=(
            "Exports contain project workflow evidence. Draft or unverified data remains explicitly labelled "
            "and must not be treated as statutory ownership or boundary certification."
        )
    )
    items: list[ExportDescriptor]
