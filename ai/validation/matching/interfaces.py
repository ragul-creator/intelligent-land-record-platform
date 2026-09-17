"""Interfaces for Phase E.1 duplicate and master-data validation.

Concrete government/master database connectors are intentionally deferred to
later integration phases; these contracts let validation code depend on stable
interfaces without pretending a live government API exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


@dataclass(frozen=True)
class DuplicateCandidate:
    candidate_id: str
    score: float
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("duplicate score must be between 0 and 1")


class DuplicateDetector(Protocol):
    def find_candidates(
        self,
        *,
        entity_type: str,
        entity_id: str,
        values: Mapping[str, object],
    ) -> Sequence[DuplicateCandidate]: ...


@dataclass(frozen=True)
class MasterDataCheck:
    valid: bool
    source_name: str
    source_reference: str | None = None
    normalized_value: object | None = None
    message: str | None = None


class MasterDataVerifier(Protocol):
    def verify(
        self,
        *,
        field_name: str,
        value: object,
        context: Mapping[str, object],
    ) -> MasterDataCheck: ...
