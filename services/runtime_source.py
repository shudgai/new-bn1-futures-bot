"""Capture startup provenance; later filesystem changes cannot rewrite it."""
import hashlib
from pathlib import Path
import subprocess
import sys
import time


def capture_runtime_source():
    from core.config import PAPER_TRADING, USE_TESTNET
    from core.services.entry_gate_integrity import VERSION
    root = Path(__file__).resolve().parents[1]

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode().strip()

    files = git("ls-files", "--", "core", "services", "tools").splitlines()
    hashes = {path: hashlib.sha256((root/path).read_bytes()).hexdigest()
              for path in files if path.endswith(".py")}
    return {
        "commit": git("rev-parse", "HEAD"),
        "gate_version": VERSION,
        "tree_clean_at_boot": not git("status", "--porcelain"),
        "source_hashes": hashes,
        "captured_at_ms": int(time.time()*1000),
        "python_version": sys.version.split()[0],
        "paper_trading": PAPER_TRADING,
        "use_testnet": USE_TESTNET,
    }
