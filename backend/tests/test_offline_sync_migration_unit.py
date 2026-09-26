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
