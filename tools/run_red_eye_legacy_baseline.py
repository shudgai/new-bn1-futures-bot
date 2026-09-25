"""Run unchanged adapter characterization assertions against the legacy factory.

This command explicitly selects OLD behavior. It cannot approve staged rollout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from my_package import red_eye_adapter

if __name__ == "__main__":
    print("LEGACY BASELINE ONLY: binding create_strategy to create_legacy_strategy")
    red_eye_adapter.create_strategy = red_eye_adapter.create_legacy_strategy
    raise SystemExit(pytest.main(["-q", "tests/test_red_eye_production_adapter.py"]))
