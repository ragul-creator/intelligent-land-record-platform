import pytest

from app.cli.bootstrap_admin import resolve_bootstrap_login_id
from app.services.user_identities import (
    generate_login_id,
    login_prefix_for_role,
    normalize_login_id,
    validate_login_id_for_role,
)


class SequenceSession:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar(self, statement):  # noqa: ANN001 - SQLAlchemy accepts a clause object.
        return self.value


def test_login_id_prefixes_and_zero_padded_format_are_role_aware() -> None:
    assert login_prefix_for_role("ADMIN") == "ADM"
    assert login_prefix_for_role("OFFICER") == "OFF"
    assert login_prefix_for_role("REVIEWER") == "REV"
    assert login_prefix_for_role("SURVEYOR") == "SUR"
    assert login_prefix_for_role("VIEWER") == "VWR"
    assert generate_login_id(SequenceSession(42), "OFFICER") == "OFF-TN-000042"


def test_supplied_login_ids_are_normalized_and_role_checked() -> None:
    assert normalize_login_id(" adm-tn-000042 ") == "ADM-TN-000042"
    assert validate_login_id_for_role("adm-tn-000042", "ADMIN") == "ADM-TN-000042"
    with pytest.raises(ValueError):
        normalize_login_id("ADMIN-TN-42")
    with pytest.raises(ValueError):
        validate_login_id_for_role("VWR-TN-000042", "ADMIN")


def test_bootstrap_login_id_uses_admin_override_or_sequence() -> None:
    assert resolve_bootstrap_login_id(SequenceSession(7), "adm-tn-000100") == "ADM-TN-000100"
    assert resolve_bootstrap_login_id(SequenceSession(7), None) == "ADM-TN-000007"
