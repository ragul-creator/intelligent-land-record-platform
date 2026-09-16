"""Phase C.6 parcel topology, cleanup, comparison, and edit-validation utilities."""

from .validation import (
    ComparisonResult,
    EditValidationResult,
    TopologyIssue,
    TopologyReport,
    compare_parcels,
    normalize_parcel_geometry,
    validate_edited_parcel,
    validate_parcel_topology,
)

__all__ = [
    "ComparisonResult",
    "EditValidationResult",
    "TopologyIssue",
    "TopologyReport",
    "compare_parcels",
    "normalize_parcel_geometry",
    "validate_edited_parcel",
    "validate_parcel_topology",
]
