"""Check the remaining explicit unsupported legacy capability.

Inline legacy profit branches were retired. This check cannot approve staged rollout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

if __name__ == "__main__":
    print("LEGACY CAPABILITY ONLY: retired inline branches are unavailable")
    raise SystemExit(pytest.main(["-q", "tests/test_red_eye_production_adapter.py",
                                 "-k", "legacy_missing_reconcile"]))
