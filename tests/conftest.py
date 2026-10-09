"""Global HARD SAFETY test isolation for account state files.

1. Per-test guard: any BinanceTestnetAccount whose state path resolves to the
   production default (data/testnet_account.json) raises immediately, so a
   test can never read or write production account state. Patching is on the
   class method within the test process only (no shared file, no race).
2. Session sentinel: SHA256 / mtime / size / existence of the production
   testnet state file are recorded at session start and verified at the end;
   any difference fails the session.
"""
import hashlib
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTION_TESTNET_STATE_FILE = os.path.join(REPO_ROOT, "data", "testnet_account.json")
_PROTECTED = (
    PRODUCTION_TESTNET_STATE_FILE,
    PRODUCTION_TESTNET_STATE_FILE + ".tmp",
    PRODUCTION_TESTNET_STATE_FILE + ".staged",
)
PRODUCTION_TELEMETRY_FILE = os.path.join(REPO_ROOT, "logs", "profit_exit_telemetry.jsonl")
_SENTINEL_PATHS = _PROTECTED + (PRODUCTION_TELEMETRY_FILE,)
TELEMETRY_LOGGER_NAME = "ProfitExitTelemetry"
_sentinel_before = {}


def _fingerprint(path):
    if not os.path.exists(path):
        return {"exists": False}
    if os.path.isdir(path):
        return {"exists": True, "dir": True, "mtime_ns": os.stat(path).st_mtime_ns}
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    stat = os.stat(path)
    return {"exists": True, "sha256": digest, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def pytest_sessionstart(session):
    for path in _SENTINEL_PATHS:
        _sentinel_before[path] = _fingerprint(path)


def pytest_sessionfinish(session, exitstatus):
    changed = [p for p in _SENTINEL_PATHS if _fingerprint(p) != _sentinel_before.get(p)]
    if changed:
        session.exitstatus = 1
        print(f"\n[TEST_ISOLATION] PRODUCTION STATE TOUCHED BY TESTS: {changed}")


def _is_protected(path):
    real = os.path.realpath(str(path))
    return any(real == os.path.realpath(p) for p in _PROTECTED)


@pytest.fixture(autouse=True)
def _forbid_production_testnet_state(monkeypatch):
    try:
        from core.testnet_account import BinanceTestnetAccount
    except Exception:
        yield
        return
    original = BinanceTestnetAccount.__dict__["_state_path"]

    def guarded(self):
        path = original(self)
        if _is_protected(path):
            raise RuntimeError(
                "[TEST_ISOLATION] BinanceTestnetAccount resolved the production state "
                "path; pass state_file=<tmp_path> in tests")
        return path

    monkeypatch.setattr(BinanceTestnetAccount, "_state_path", guarded)
    yield


@pytest.fixture
def testnet_state_file(tmp_path):
    """Per-test isolated state path for BinanceTestnetAccount(state_file=...)."""
    return str(tmp_path / "testnet_account.json")


def _is_production_telemetry(path):
    try:
        return os.path.realpath(os.fspath(path)) == os.path.realpath(PRODUCTION_TELEMETRY_FILE)
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _isolate_profit_exit_telemetry(monkeypatch, tmp_path):
    """Global test-only isolation for ProfitExitTelemetry (no production change).

    - TELEMETRY_DIR / TELEMETRY_FILE module globals are redirected to tmp_path,
      so get_telemetry_logger() builds its FileHandler on a per-test file.
    - logging.FileHandler is fail-closed against the production telemetry path.
    - Logger handlers / level / propagate / disabled and ProfitExitTelemetry.ENABLED
      are snapshotted and restored; handlers created during the test are closed.
    """
    import logging

    logger = logging.getLogger(TELEMETRY_LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_level, saved_propagate, saved_disabled = logger.level, logger.propagate, logger.disabled

    telemetry_cls = None
    saved_enabled = None
    try:
        import core.services.exits.profit_exit_telemetry as telemetry_mod
    except Exception:
        telemetry_mod = None
    if telemetry_mod is not None:
        test_dir = tmp_path / "telemetry_logs"
        monkeypatch.setattr(telemetry_mod, "TELEMETRY_DIR", str(test_dir))
        monkeypatch.setattr(telemetry_mod, "TELEMETRY_FILE", str(test_dir / "profit_exit_telemetry.jsonl"))
        telemetry_cls = telemetry_mod.ProfitExitTelemetry
        saved_enabled = telemetry_cls.ENABLED

    real_file_handler = logging.FileHandler

    class _GuardedFileHandler(real_file_handler):
        def __init__(self, filename, *args, **kwargs):
            if _is_production_telemetry(filename):
                raise RuntimeError(
                    "[TEST_ISOLATION] attempted to open production telemetry log")
            # Explicit base call: stdlib BaseRotatingHandler invokes
            # logging.FileHandler.__init__(self, ...) on non-subclass instances.
            real_file_handler.__init__(self, filename, *args, **kwargs)

    monkeypatch.setattr(logging, "FileHandler", _GuardedFileHandler)

    import logging.handlers
    real_rotating_handler = logging.handlers.RotatingFileHandler

    class _GuardedRotatingFileHandler(real_rotating_handler):
        def __init__(self, filename, *args, **kwargs):
            if _is_production_telemetry(filename):
                raise RuntimeError(
                    "[TEST_ISOLATION] attempted to open production telemetry log")
            real_rotating_handler.__init__(self, filename, *args, **kwargs)

    monkeypatch.setattr(logging.handlers, "RotatingFileHandler", _GuardedRotatingFileHandler)

    # Start every test with no inherited telemetry handlers.
    logger.handlers = []
    try:
        yield
    finally:
        for handler in list(logger.handlers):
            if handler not in saved_handlers:
                logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
        logger.handlers = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate
        logger.disabled = saved_disabled
        if telemetry_cls is not None:
            telemetry_cls.ENABLED = saved_enabled


@pytest.fixture
def telemetry_log_file():
    """Path of the per-test isolated telemetry file (for payload assertions)."""
    import core.services.exits.profit_exit_telemetry as telemetry_mod
    return telemetry_mod.TELEMETRY_FILE
