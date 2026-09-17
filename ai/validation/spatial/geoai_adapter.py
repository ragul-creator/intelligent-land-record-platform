"""Adapter from existing C.6 topology issues to the unified E.1 contract."""

from __future__ import annotations

from ai.geoai.topology.validation import TopologyIssue
from ai.validation.models import EvidenceReference, Severity, ValidationIssue


_SEVERITY_MAP = {
    "ERROR": Severity.ERROR,
    "REVIEW": Severity.ERROR,
    "WARNING": Severity.WARNING,
    "INFO": Severity.INFO,
}


def from_topology_issue(
    issue: TopologyIssue,
    *,
    source_reference: str | None = None,
) -> tuple[ValidationIssue, ...]:
    """Convert one topology issue to one unified issue per referenced parcel."""

    severity = _SEVERITY_MAP.get(issue.severity.upper(), Severity.WARNING)
    parcel_ids = issue.parcel_ids or ("UNKNOWN",)
    evidence = (
        EvidenceReference(source_type="GIS_TOPOLOGY", source_id=source_reference),
    ) if source_reference else ()
    return tuple(
        ValidationIssue(
            code=issue.code,
            severity=severity,
            message=issue.message,
            entity_type="PARCEL",
            entity_id=parcel_id,
            review_required=issue.severity.upper() in {"ERROR", "REVIEW"},
            evidence=evidence,
            rule_id=f"GEOAI.{issue.code}",
            metadata={"area_m2": issue.area_m2} if issue.area_m2 is not None else {},
        )
        for parcel_id in parcel_ids
    )


def from_topology_issues(
    issues: tuple[TopologyIssue, ...],
    *,
    source_reference: str | None = None,
) -> tuple[ValidationIssue, ...]:
    converted: list[ValidationIssue] = []
    for issue in issues:
        converted.extend(from_topology_issue(issue, source_reference=source_reference))
    return tuple(converted)
