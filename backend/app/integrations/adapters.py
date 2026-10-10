"""DTO-only demo adapters. They never make network requests or certify data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AdapterState(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    DEMO = "DEMO"
    READY = "READY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Capability:
    name: str
    state: AdapterState
    supports_acknowledgement: bool
    network_enabled: bool = False


class DemoAdapter:
    name = "demo"
    label = "Demo adapter"

    def capability(self) -> Capability:
        return Capability(self.name, AdapterState.DEMO, True)

    def validate(self, payload: dict[str, Any]) -> None:
        if payload.get("project_id") is None or not isinstance(payload.get("features"), list):
            raise ValueError("Integration payload requires project_id and features.")
        if _contains_credentials(payload):
            raise ValueError("Integration payload must not include credentials or secrets.")

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.validate(payload)
        return {"outcome": "DEMO_ACCEPTED", "adapter": self.name, "acknowledgement_id": f"demo:{payload['project_id']}:{len(payload['features'])}", "network_called": False}


class GenericGISAdapter(DemoAdapter):
    name, label = "generic_gis", "Generic GIS demo"


class LRMSDemoAdapter(DemoAdapter):
    name, label = "lrms_demo", "LRMS demo"


class DILRMPDemoAdapter(DemoAdapter):
    name, label = "dilrmp_demo", "DILRMP demo (non-live)"


_ADAPTERS = {adapter.name: adapter for adapter in (GenericGISAdapter(), LRMSDemoAdapter(), DILRMPDemoAdapter())}


def _contains_credentials(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            any(marker in str(key).casefold() for marker in ("password", "secret", "token", "api_key", "authorization"))
            or _contains_credentials(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_credentials(item) for item in value)
    return False


def get_adapter(name: str) -> DemoAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError as error:
        raise ValueError("Unknown integration adapter.") from error


def capabilities() -> list[Capability]:
    return [adapter.capability() for adapter in _ADAPTERS.values()]
