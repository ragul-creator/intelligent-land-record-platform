"""Cross-branch invariants that catch broken upgrades and deletion ordering."""

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.database import Base
import app.models  # noqa: F401
from app.services.project_deletion import _DELETE_STATEMENTS


def test_combined_history_has_one_head_reachable_from_both_branch_heads():
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["20261009_14"]
    for previous_head in ("20260928_12", "20260926_13"):
        assert list(scripts.iterate_revisions("head", previous_head))


def test_project_deletion_references_real_tables_in_foreign_key_safe_order():
    names = [re.match(r"DELETE FROM (\w+)", statement).group(1) for statement in _DELETE_STATEMENTS]
    for name in names:
        assert name in Base.metadata.tables, f"Deletion references absent table {name}"
        for fk in Base.metadata.tables[name].foreign_keys:
            parent = fk.column.table.name
            if parent in names and fk.ondelete not in {"CASCADE", "SET NULL"}:
                assert names.index(name) < names.index(parent), f"Delete {name} before {parent}"
