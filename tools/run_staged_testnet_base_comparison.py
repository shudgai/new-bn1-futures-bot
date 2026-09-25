"""Run unchanged account tests against the historical account module in memory."""

from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core
import pytest

source = subprocess.check_output(
    ["git", "show", "fe8b72bc:core/testnet_account.py"], cwd=ROOT, text=True,
)
module = types.ModuleType("core.testnet_account")
module.__file__ = str(ROOT / "core/testnet_account.py")
sys.modules[module.__name__] = module
core.testnet_account = module
exec(compile(source, module.__file__, "exec"), module.__dict__)

if __name__ == "__main__":
    raise SystemExit(pytest.main([
        "-q", "--tb=no", str(ROOT / "tests/test_testnet_account.py"),
        f"--junitxml={ROOT / 'reports/red_eye_production/testnet_base_comparison.xml'}",
    ]))
