"""Typed contracts for the Phase G.2 role-aware project dashboard."""

import uuid
from typing import Literal

from pydantic import BaseModel

from app.schemas.projects import ProjectResponse, StatusCount

ApplicationRole = Literal["ADMIN", "OFFICER", "REVIEWER", "SURVEYOR", "VIEWER"]


class DashboardDocumentMetrics(BaseModel):
    total: int
    by_status: list[StatusCount]
    validated_records: int
    unlinked_validated_records: int


class DashboardReviewMetrics(BaseModel):
    open: int
    document: int
    gis: int
    record_parcel_link: int
    high: int
    medium: int
    assigned_to_me: int


class DashboardGeoMetrics(BaseModel):
    imagery_assets: int
    geoai_jobs: int
    parcels: int
    buildings: int
    roads: int
    land_use_features: int
    parcel_statuses: list[StatusCount]


class DashboardLinkMetrics(BaseModel):
    total: int
    by_status: list[StatusCount]
    confirmed_records: int


class DashboardJobMetrics(BaseModel):
    total: int
    by_status: list[StatusCount]


class DashboardAttentionMetrics(BaseModel):
    failed_jobs: int
    review_required_documents: int
    high_open_reviews: int
    ambiguous_record_parcel_links: int
    parcels_needing_review: int


class ProjectDashboardResponse(BaseModel):
    project: ProjectResponse
    project_role: ApplicationRole
    documents: DashboardDocumentMetrics
    reviews: DashboardReviewMetrics
    geo: DashboardGeoMetrics
    record_parcel_links: DashboardLinkMetrics
    jobs: DashboardJobMetrics
    attention: DashboardAttentionMetrics
