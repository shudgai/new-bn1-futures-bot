"""單元測試：同方向重新進場冷卻鎖、跨重啟狀態持久化與行情資料新鮮度保護。"""
import json
import os
import tempfile
import time
import pytest
from unittest.mock import MagicMock, patch

from core.services.reverse_entry_cooldown_lock import (
    check_same_direction_reentry_cooldown,
    check_closed_candle_freshness,
    check_reverse_cooldown_lock,
    evaluate_reverse_cooldown_guard,
)


def test_block_short_reentry_after_48_seconds():
    """平空後 48 秒內（冷卻 120 秒），必須被阻擋，剩餘 72 秒。"""
    trade = {
        "side": "short",
        "time": 1000.0,
        "timestamp_ms": 1_000_000,
    }
    allowed, reason = check_same_direction_reentry_cooldown(
        "OPEN_SHORT", trade, 120.0, now_ts=1048.0,
    )
    assert allowed is False
    assert "remaining=72.0s" in reason
    assert "elapsed=48.0s" in reason


def test_allow_short_reentry_after_120_seconds():
    """平空後 120 秒到期，必須放行。"""
    trade = {
        "side": "short",
        "time": 1000.0,
    }
    allowed, reason = check_same_direction_reentry_cooldown(
        "OPEN_SHORT", trade, 120.0, now_ts=1120.0,
    )
    assert allowed is True
    assert reason is None

    # 超過 120 秒（例如 150 秒）也放行
    allowed_late, reason_late = check_same_direction_reentry_cooldown(
        "OPEN_SHORT", trade, 120.0, now_ts=1150.0,
    )
    assert allowed_late is True
    assert reason_late is None


def test_block_long_reentry_within_cooldown():
    """平多後 60 秒內（冷卻 120 秒），必須被阻擋，剩餘 60 秒。"""
    trade = {
        "side": "long",
        "time": 2000.0,
        "timestamp_ms": 2_000_000,
    }
    allowed, reason = check_same_direction_reentry_cooldown(
        "OPEN_LONG", trade, 120.0, now_ts=2060.0,
    )
    assert allowed is False
    assert "remaining=60.0s" in reason
    assert "elapsed=60.0s" in reason


def test_allow_long_reentry_after_cooldown():
    """平多後 120 秒到期，必須放行。"""
    trade = {
        "side": "long",
        "time": 2000.0,
    }
    allowed, reason = check_same_direction_reentry_cooldown(
        "OPEN_LONG", trade, 120.0, now_ts=2121.0,
    )
    assert allowed is True
    assert reason is None


def test_opposite_direction_bypasses_same_direction_cooldown():
    """平多後想開空 (反手)，不在此處阻擋（由反向冷卻鎖負責）。"""
    trade = {
        "side": "long",
        "time": 1000.0,
    }
    # 剛平多，目標開空：同方向冷卻放行
    allowed, reason = check_same_direction_reentry_cooldown(
        "OPEN_SHORT", trade, 120.0, now_ts=1010.0,
    )
    assert allowed is True
    assert reason is None

    # 剛平空，目標開多：同方向冷卻放行
    trade_short = {"side": "short", "time": 1000.0}
    allowed_flip, reason_flip = check_same_direction_reentry_cooldown(
        "OPEN_LONG", trade_short, 120.0, now_ts=1010.0,
    )
    assert allowed_flip is True
    assert reason_flip is None


def test_invalid_timestamps_fail_closed():
    """異常、未來或無效時間戳採取 Fail-Closed 保守拒絕策略。"""
    # 未來時間戳 (now < exit_ts)
    trade = {"side": "short", "time": 2000.0}
    allowed, reason = check_same_direction_reentry_cooldown(
        "OPEN_SHORT", trade, 120.0, now_ts=1900.0,
    )
    assert allowed is False
    assert "INVALID_REENTRY_COOLDOWN_TIMESTAMP" in reason

    # 無效時間戳 (0 或負數)
    trade_zero = {"side": "short", "time": 0.0}
    allowed_zero, reason_zero = check_same_direction_reentry_cooldown(
        "OPEN_SHORT", trade_zero, 120.0, now_ts=2000.0,
    )
    assert allowed_zero is False
    assert "INVALID_REENTRY_COOLDOWN_TIMESTAMP" in reason_zero

    # NaN / Inf
    trade_nan = {"side": "short", "time": float("nan")}
    allowed_nan, reason_nan = check_same_direction_reentry_cooldown(
        "OPEN_SHORT", trade_nan, 120.0, now_ts=2000.0,
    )
    assert allowed_nan is False
    assert "INVALID_REENTRY_COOLDOWN_TIMESTAMP" in reason_nan


def test_candle_freshness_1m_candle():
    """驗證 1m K 線新鮮度判定：
    - 1m 收盤後 60 秒（延遲 <= 90s）：放行
    - 1m 收盤後 91 秒（延遲 > 90s）：阻擋
    - K 線尚未收盤：阻擋
    """
    timeframe_ms = 60_000
    stamp_open_ms = 1_000_000.0
    stamp_close_ms = stamp_open_ms + timeframe_ms  # 1_060_000

    # 1. 收盤後 60 秒 -> 放行 (60s <= 90s)
    now_60s = stamp_close_ms + 60_000.0
    allowed, reason = check_closed_candle_freshness(
        last_candle_timestamp_ms=stamp_open_ms,
        timeframe_ms=timeframe_ms,
        timestamp_is_open_time=True,
        now_ms=now_60s,
    )
    assert allowed is True
    assert reason is None

    # 2. 收盤後 91 秒 -> 阻擋 (91s > 90s)
    now_91s = stamp_close_ms + 91_000.0
    allowed_stale, reason_stale = check_closed_candle_freshness(
        last_candle_timestamp_ms=stamp_open_ms,
        timeframe_ms=timeframe_ms,
        timestamp_is_open_time=True,
        now_ms=now_91s,
    )
    assert allowed_stale is False
    assert "STALE_CANDLE_DATA" in reason_stale
    assert "elapsed=91.0s" in reason_stale

    # 3. K 線尚未收盤 (now < close_ms) -> 阻擋
    now_unclosed = stamp_open_ms + 30_000.0
    allowed_unclosed, reason_unclosed = check_closed_candle_freshness(
        last_candle_timestamp_ms=stamp_open_ms,
        timeframe_ms=timeframe_ms,
        timestamp_is_open_time=True,
        now_ms=now_unclosed,
    )
    assert allowed_unclosed is False
    assert "CANDLE_NOT_YET_CLOSED" in reason_unclosed


def test_candle_freshness_15m_candle():
    """驗證 15m 大週期 K 線新鮮度判定：
    - 15m 收盤後 300 秒（5 分鐘）：放行
    - 15m 收盤後 1000 秒（超過 960 秒上限）：阻擋
    """
    timeframe_ms = 900_000  # 15m = 900s
    stamp_open_ms = 10_000_000.0
    stamp_close_ms = stamp_open_ms + timeframe_ms  # 10_900_000

    # 收盤後 5 分鐘 (300s) -> 放行
    now_5m = stamp_close_ms + 300_000.0
    allowed, reason = check_closed_candle_freshness(
        last_candle_timestamp_ms=stamp_open_ms,
        timeframe_ms=timeframe_ms,
        timestamp_is_open_time=True,
        now_ms=now_5m,
    )
    assert allowed is True
    assert reason is None

    # 收盤後 1000 秒 -> 阻擋 (1000s > 960s 預設上限)
    now_stale = stamp_close_ms + 1_000_000.0
    allowed_stale, reason_stale = check_closed_candle_freshness(
        last_candle_timestamp_ms=stamp_open_ms,
        timeframe_ms=timeframe_ms,
        timestamp_is_open_time=True,
        now_ms=now_stale,
    )
    assert allowed_stale is False
    assert "STALE_CANDLE_DATA" in reason_stale


def test_candle_freshness_explicit_close_time():
    """驗證 timestamp_is_open_time=False 時直接將 stamp 視為收盤時間。"""
    timeframe_ms = 60_000
    stamp_close_ms = 1_060_000.0

    # 延遲 45 秒 -> 放行
    now_45s = stamp_close_ms + 45_000.0
    allowed, reason = check_closed_candle_freshness(
        last_candle_timestamp_ms=stamp_close_ms,
        timeframe_ms=timeframe_ms,
        timestamp_is_open_time=False,
        now_ms=now_45s,
    )
    assert allowed is True
    assert reason is None


def test_state_persistence_last_closed_trade_details():
    """驗證 last_closed_trade_details 在 save_state 與 _load_state 流程中完整跨重啟保存與恢復。"""
    from core.testnet_account import BinanceTestnetAccount

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "test_state.json")
        mock_ex = MagicMock()
        # 1. 建立帳戶實例，模擬平倉狀態
        acc = BinanceTestnetAccount(mock_ex, state_file=state_path)
        exit_time = time.time() - 30.0  # 30 秒前平倉
        acc.last_closed_trades["BTC/USDT"] = {
            "side": "short",
            "price": 65000.0,
            "bar_index": 500,
            "time": exit_time,
            "timestamp_ms": int(exit_time * 1000),
            "reason": "EXIT_BY_TREND_BROKEN",
        }
        acc.save_state(strict=True)

        # 驗證 JSON 檔案確實寫入包含 last_closed_trade_details
        with open(state_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert "last_closed_trade_details" in saved_data
        details = saved_data["last_closed_trade_details"]
        assert "BTC/USDT" in details
        assert details["BTC/USDT"]["side"] == "short"
        assert details["BTC/USDT"]["price"] == 65000.0
        assert details["BTC/USDT"]["reason"] == "EXIT_BY_TREND_BROKEN"

        # 2. 模擬重啟，新實例載入狀態
        acc_restarted = BinanceTestnetAccount(mock_ex, state_file=state_path)
        assert hasattr(acc_restarted, "last_closed_trades")
        assert "BTC/USDT" in acc_restarted.last_closed_trades
        reloaded = acc_restarted.last_closed_trades["BTC/USDT"]
        assert reloaded["side"] == "short"
        assert reloaded["price"] == 65000.0
        assert reloaded["reason"] == "EXIT_BY_TREND_BROKEN"
        assert abs(reloaded["time"] - exit_time) < 0.001

        # 3. 驗證同方向冷卻在重啟後依然有效阻擋 (剛過 30 秒，120 秒未到)
        allowed, reason = check_same_direction_reentry_cooldown(
            "OPEN_SHORT", reloaded, 120.0, now_ts=exit_time + 48.0,
        )
        assert allowed is False
        assert "remaining=72.0s" in reason


def test_open_position_ema_cross_cannot_bypass_same_direction_cooldown():
    """驗證 open_position 中 EMA_CROSS 與 allow_flip 絕無法繞過同方向冷卻鎖。"""
    import asyncio
    from core.testnet_account import BinanceTestnetAccount

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "test_state.json")
            mock_ex = MagicMock()
            acc = BinanceTestnetAccount(mock_ex, state_file=state_path)
            # 設定剛剛平空
            now = time.time()
            acc.last_closed_trades["BTC/USDT"] = {
                "side": "short",
                "price": 60000.0,
                "time": now - 40.0,  # 40 秒前平空
                "timestamp_ms": int((now - 40.0) * 1000),
                "reason": "EXIT_BY_TREND_BROKEN",
            }

            # 嘗試以 EMA_CROSS / allow_flip 開空
            entry_ctx = {
                "entry_source": "EMA_CROSS_FAST",
                "allow_flip": True,
            }
            res = await acc.open_position(
                symbol="BTC/USDT",
                side="SHORT",
                price=60000.0,
                amount_usdt=100.0,
                sl=61000.0,
                tp=58000.0,
                reason="EMA_CROSS_ENTRY",
                entry_context=entry_ctx,
            )
            # 必須被第一層同方向冷卻鎖攔截返回 False！
            assert res is False

    asyncio.run(_run())

