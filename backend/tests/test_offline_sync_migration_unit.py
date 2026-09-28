"""Unit tests for Alembic migration 20260926_13_offline_sync upgrade and downgrade."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_migration_module():
    migration_path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "20260926_13_offline_sync.py"
    spec = importlib.util.spec_from_file_location("migration_20260926_13", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_metadata() -> None:
    mod = _load_migration_module()
    assert mod.revision == "20260926_13"
    assert mod.down_revision == "20260926_12"
    assert mod.branch_labels is None
    assert mod.depends_on is None


def test_migration_upgrade_and_downgrade_execution() -> None:
    mod = _load_migration_module()
    with patch("alembic.op.create_table") as mock_create_table, \
         patch("alembic.op.create_index") as mock_create_index, \
         patch("alembic.op.drop_table") as mock_drop_table:
        
        # Test upgrade
        mod.upgrade()
        assert mock_create_table.call_count == 2
        table_names = [call[0][0] for call in mock_create_table.call_args_list]
        assert "sync_operations" in table_names
        assert "sync_changes" in table_names
        assert mock_create_index.call_count == 6

        # Test downgrade
        mod.downgrade()
        assert mock_drop_table.call_count == 2
        dropped_names = [call[0][0] for call in mock_drop_table.call_args_list]
        assert dropped_names == ["sync_changes", "sync_operations"]


def test_migration_12_upgrade_and_downgrade_execution() -> None:
    migration_path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "20260926_12_gis_imports.py"
    spec = importlib.util.spec_from_file_location("migration_20260926_12", migration_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.revision == "20260926_12"
    assert mod.down_revision == "20260926_11"

    with patch("alembic.op.create_table") as mock_create_table, \
         patch("alembic.op.create_index") as mock_create_index, \
         patch("alembic.op.drop_table") as mock_drop_table:
        mod.upgrade()
        assert mock_create_table.call_count == 1
        assert mock_create_table.call_args[0][0] == "gis_import_runs"
        assert mock_create_index.call_count == 4

        mod.downgrade()
        assert mock_drop_table.call_count == 1
        assert mock_drop_table.call_args[0][0] == "gis_import_runs"


def test_migration_11_upgrade_and_downgrade_execution() -> None:
    migration_path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "20260926_11_geopackage_data_contracts.py"
    spec = importlib.util.spec_from_file_location("migration_20260926_11", migration_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.revision == "20260926_11"
    assert mod.down_revision == "20260922_10"

    with patch("alembic.op.drop_constraint") as mock_drop_constraint, \
         patch("alembic.op.create_check_constraint") as mock_create_constraint:
        mod.upgrade()
        assert mock_drop_constraint.call_count == 2
        assert mock_create_constraint.call_count == 2

        mod.downgrade()
        assert mock_drop_constraint.call_count == 4
        assert mock_create_constraint.call_count == 4
