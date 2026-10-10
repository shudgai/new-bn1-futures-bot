"""Bounded-logging coverage for ProfitExitTelemetry (rotation only).

All file I/O targets tmp_path: the global conftest fixture redirects
TELEMETRY_DIR / TELEMETRY_FILE per test and fail-closes on the production path.
No 10 MB data is generated; rollover is exercised with a tiny threshold.
"""
import hashlib
import json
import logging
import logging.handlers
import os
from unittest.mock import patch

import pytest

import core.services.exits.profit_exit_telemetry as telemetry_mod
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
from core.services.exits.profit_exit_telemetry import ProfitExitTelemetry, get_telemetry_logger

# Captured at import (collection) time, before the conftest guard subclass is installed.
REAL_ROTATING_FILE_HANDLER = logging.handlers.RotatingFileHandler
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTION_TELEMETRY_FILE = os.path.join(REPO_ROOT, 'logs', 'profit_exit_telemetry.jsonl')
PAYLOAD_KEYS = {'timestamp_ms', 'position_id', 'event_type', 'data'}
HOLD_RELEASED = ('core.services.exits.trend_hold_evaluator.evaluate_trend_hold')


def _fingerprint(path):
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    st = os.stat(path)
    return digest, st.st_size, st.st_mtime_ns


@pytest.fixture
def production_log_guard():
    before = _fingerprint(PRODUCTION_TELEMETRY_FILE)
    yield
    assert _fingerprint(PRODUCTION_TELEMETRY_FILE) == before


def _telemetry_handlers():
    return logging.getLogger('ProfitExitTelemetry').handlers


def _create_pos():
    return {'id': 't1', 'symbol': 'BTCUSDT', 'side': 'LONG', 'entry_price': 100, 'qty': 1,
            'entry_atr': 1, 'sl': 90, 'open_timestamp': 1}


def _pullback_decision():
    pos = _create_pos()
    evaluate_peak_trailing(pos, 105.0, {'quote_ms': 1000}, atr=1.0)
    return evaluate_peak_trailing(pos, 104.0, {'quote_ms': 1001}, atr=1.0)


# 1-3. handler type and bounded settings (and 9: path is tmp, never production)
def test_handler_is_bounded_rotating_file_handler(tmp_path, production_log_guard):
    logger = get_telemetry_logger()
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, REAL_ROTATING_FILE_HANDLER)
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 5
    assert telemetry_mod.TELEMETRY_MAX_BYTES == 10 * 1024 * 1024
    assert telemetry_mod.TELEMETRY_BACKUP_COUNT == 5
    assert os.path.realpath(handler.baseFilename).startswith(os.path.realpath(str(tmp_path)))
    assert os.path.realpath(handler.baseFilename) != os.path.realpath(PRODUCTION_TELEMETRY_FILE)
    assert logger.name == 'ProfitExitTelemetry'


# 4. no duplicate handlers / no FD growth
def test_repeated_get_logger_no_duplicate_handlers(production_log_guard):
    first = get_telemetry_logger()
    handler = first.handlers[0]
    fd_dir = '/proc/self/fd'
    fds_before = len(os.listdir(fd_dir)) if os.path.isdir(fd_dir) else None
    for _ in range(200):
        assert get_telemetry_logger() is first
    assert first.handlers == [handler]
    if fds_before is not None:
        assert len(os.listdir(fd_dir)) == fds_before


# 5. payload schema unchanged and valid JSONL
def test_payload_valid_json_schema_unchanged(telemetry_log_file, production_log_guard):
    ProfitExitTelemetry.ENABLED = True
    ProfitExitTelemetry.log_event('pid-1', 'PARABOLIC_ARM_REACHED', {'symbol': 'X', 'v': 1.5})
    ProfitExitTelemetry.log_event('pid-2', 'FINAL_EXIT_AUTHORIZED', {'symbol': 'Y'})
    for h in _telemetry_handlers():
        h.flush()
    with open(telemetry_log_file) as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert all(set(r) == PAYLOAD_KEYS for r in rows)
    assert rows[0]['position_id'] == 'pid-1'
    assert rows[0]['event_type'] == 'PARABOLIC_ARM_REACHED'
    assert rows[0]['data'] == {'symbol': 'X', 'v': 1.5}
    assert isinstance(rows[0]['timestamp_ms'], float)


# 6. logger init failure is fail-open (and 8: decisions identical)
@patch(HOLD_RELEASED, return_value=('RELEASED', ''))
def test_logger_init_failure_fail_open(mock_hold, production_log_guard):
    ProfitExitTelemetry.ENABLED = False
    expected = _pullback_decision()
    assert expected is None

    ProfitExitTelemetry.ENABLED = True
    with patch('logging.handlers.RotatingFileHandler', side_effect=OSError('init boom')):
        ProfitExitTelemetry.log_event('pid', 'X', {})  # must not raise
        assert get_telemetry_logger().handlers == []
        actual = _pullback_decision()
    assert actual == expected

    with patch.object(telemetry_mod.os, 'makedirs', side_effect=PermissionError('mkdir boom')):
        ProfitExitTelemetry.log_event('pid', 'X', {})  # must not raise
        assert _pullback_decision() == expected


# 7. emit / rotation failure is fail-open (and 8: decisions identical)
@patch(HOLD_RELEASED, return_value=('RELEASED', ''))
def test_logger_emit_failure_fail_open(mock_hold, production_log_guard):
    ProfitExitTelemetry.ENABLED = False
    expected = _pullback_decision()
    assert expected is None

    ProfitExitTelemetry.ENABLED = True
    logger = get_telemetry_logger()
    with patch.object(logger, 'info', side_effect=RuntimeError('emit boom')):
        ProfitExitTelemetry.log_event('pid', 'X', {})  # must not raise
        assert _pullback_decision() == expected

    handler = logger.handlers[0]
    with patch.object(logging, 'raiseExceptions', False), \
            patch.object(type(handler), 'shouldRollover', side_effect=OSError('rollover boom')), \
            patch.object(type(handler), 'doRollover', side_effect=OSError('rollover boom')):
        ProfitExitTelemetry.log_event('pid', 'X', {})  # must not raise
        assert _pullback_decision() == expected


# 8. enabled vs disabled decisions identical with real (tmp) handler
@patch(HOLD_RELEASED, return_value=('RELEASED', ''))
def test_enabled_disabled_decisions_identical(mock_hold, production_log_guard):
    ProfitExitTelemetry.ENABLED = False
    disabled = _pullback_decision()
    ProfitExitTelemetry.ENABLED = True
    enabled = _pullback_decision()
    assert disabled == enabled
    assert enabled is None


# 9. natural rollover with a tiny threshold, tmp_path only
def test_rotation_bounded_tmp_path_only(monkeypatch, tmp_path, telemetry_log_file, production_log_guard):
    assert os.path.realpath(telemetry_log_file).startswith(os.path.realpath(str(tmp_path)))
    monkeypatch.setattr(telemetry_mod, 'TELEMETRY_MAX_BYTES', 400)
    monkeypatch.setattr(telemetry_mod, 'TELEMETRY_BACKUP_COUNT', 2)
    ProfitExitTelemetry.ENABLED = True
    for i in range(60):
        ProfitExitTelemetry.log_event(f'pid-{i}', 'NEW_RUNTIME_PEAK', {'i': i, 'pad': 'x' * 40})
    handler = _telemetry_handlers()[0]
    assert handler.maxBytes == 400 and handler.backupCount == 2
    handler.flush()

    log_dir = os.path.dirname(telemetry_log_file)
    base = os.path.basename(telemetry_log_file)
    files = sorted(os.listdir(log_dir))
    assert files == sorted([base, base + '.1', base + '.2'])  # at most backupCount backups
    for name in files:
        path = os.path.join(log_dir, name)
        assert os.path.getsize(path) <= 400
        with open(path) as fh:
            for line in fh.read().splitlines():
                assert set(json.loads(line)) == PAYLOAD_KEYS
    # Newest record lives in the active file.
    with open(telemetry_log_file) as fh:
        assert json.loads(fh.read().splitlines()[-1])['position_id'] == 'pid-59'


# 10. production telemetry file untouched by the whole module
def test_production_log_not_opened(production_log_guard):
    with pytest.raises(RuntimeError):
        logging.handlers.RotatingFileHandler(PRODUCTION_TELEMETRY_FILE, maxBytes=1, backupCount=1)
