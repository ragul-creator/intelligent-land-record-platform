"""Backend application package."""

import sys
from pathlib import Path

# Ensure repository root is on sys.path so the 'ai' package is importable when running from backend
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
