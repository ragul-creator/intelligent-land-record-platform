import pytest

from app.integrations.adapters import AdapterState, capabilities, get_adapter


def test_demo_adapters_are_selectable_and_never_network_enabled() -> None:
    assert {item.name for item in capabilities()} == {"generic_gis", "lrms_demo", "dilrmp_demo"}
    assert all(item.state is AdapterState.DEMO and not item.network_enabled for item in capabilities())


def test_demo_adapter_validates_and_executes_without_network_or_credentials() -> None:
    adapter = get_adapter("dilrmp_demo")
    result = adapter.execute({"project_id": "project-1", "features": [{"id": "parcel-1", "preliminary": True}]})
    assert result["outcome"] == "DEMO_ACCEPTED"
    assert result["network_called"] is False
    with pytest.raises(ValueError, match="credentials"):
        adapter.validate({"project_id": "project-1", "features": [], "secret": "never-log-me"})


def test_unknown_adapter_is_not_silently_configured() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        get_adapter("live_dilrmp")
