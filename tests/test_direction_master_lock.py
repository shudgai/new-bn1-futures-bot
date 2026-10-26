"""Tests for Direction Master Lock (方向總開關)

Validates:
1. 07:15 V-turn scenario: Price bounces from bottom, MA3 crosses above KC middle
   and slopes upward strongly. Any SHORT signal is 100% VETOED across all layers.
2. Long requirement: MA3 > KC_middle and MA3 sloping upward.
3. Short requirement: MA3 < KC_middle and MA3 sloping downward.
4. Physical layer enforcement in testnet_account and CentralEntryGate.
"""

import asyncio
import pandas as pd
import pytest

from core.gates.direction_master_lock import (
    evaluate_direction_master_lock,
    check_direction_lock_from_frame,
)
from core.gates.central_entry_gate import CentralEntryGate
from core.services.math_execution_gate import validate_entry_at_execution


class MockTestnetAccount:
    def __init__(self):
        self.positions = {}
        self.pending_limit_orders = set()
        self.closing_lock = set()
        self.last_closed_trades = {}
        self.logs = []
        self.entry_filter_last = {}

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))


def test_direction_master_lock_pure_math():
    # Scenario: V-turn upward, MA3 above KC middle and rising
    kc_mid = 0.004270
    ma3_prev = 0.004265
    ma3_curr = 0.004278  # Above midline, slope up
    ma5_curr = 0.004272

    # SHORT must be VETOED
    short_ok, short_reason, _ = evaluate_direction_master_lock(
        side="SHORT",
        ma3_curr=ma3_curr,
        ma3_prev=ma3_prev,
        kc_middle=kc_mid,
        ma5_curr=ma5_curr,
    )
    assert short_ok is False
    assert short_reason == "BLOCKED_DIRECTION_LOCK_SHORT_ABOVE_MID"

    # LONG must be ALLOWED
    long_ok, long_reason, _ = evaluate_direction_master_lock(
        side="LONG",
        ma3_curr=ma3_curr,
        ma3_prev=ma3_prev,
        kc_middle=kc_mid,
        ma5_curr=ma5_curr,
    )
    assert long_ok is True
    assert long_reason == "DIRECTION_LOCK_PASS"


def test_direction_master_lock_downward_trend():
    # Scenario: Bearish trend, MA3 below KC middle and falling
    kc_mid = 0.004270
    ma3_prev = 0.004265
    ma3_curr = 0.004255  # Below midline, slope down
    ma5_curr = 0.004260

    # LONG must be VETOED
    long_ok, long_reason, _ = evaluate_direction_master_lock(
        side="LONG",
        ma3_curr=ma3_curr,
        ma3_prev=ma3_prev,
        kc_middle=kc_mid,
        ma5_curr=ma5_curr,
    )
    assert long_ok is False
    assert long_reason == "BLOCKED_DIRECTION_LOCK_LONG_BELOW_MID"

    # SHORT must be ALLOWED
    short_ok, short_reason, _ = evaluate_direction_master_lock(
        side="SHORT",
        ma3_curr=ma3_curr,
        ma3_prev=ma3_prev,
        kc_middle=kc_mid,
        ma5_curr=ma5_curr,
    )
    assert short_ok is True
    assert short_reason == "DIRECTION_LOCK_PASS"


def test_v_turn_short_signal_blocked_at_central_entry_gate():
    # 07:15 盤面數據模擬：價格從谷底回升，MA3 穿過中軌衝高
    bars = [
        dict(timestamp=60_000, open=0.004250, high=0.004265, low=0.004245, close=0.004260,
             atr=0.000050, ma3=0.004255, ma5=0.004252, ma15=0.004250,
             kc_upper=0.004350, kc_middle=0.004270, kc_lower=0.004190, is_closed=True),
        dict(timestamp=120_000, open=0.004260, high=0.004285, low=0.004258, close=0.004280,
             atr=0.000050, ma3=0.004275, ma5=0.004268, ma15=0.004255,
             kc_upper=0.004350, kc_middle=0.004270, kc_lower=0.004190, is_closed=True),
    ]
    df = pd.DataFrame(bars)
    df.attrs["timeframe_ms"] = 60_000.0

    # 模擬試圖開空（SHORT）
    gate_res = CentralEntryGate.verify_and_authorize(
        symbol="1000PEPEUSDT",
        side="SHORT",
        price=0.004280,
        klines_or_snapshot=df,
        source="SWING_PIVOT_SHORT",
    )

    # 驗證 CentralEntryGate 100% 一票否決開空
    assert gate_res.passed is False
    assert "DIRECTION_LOCK" in gate_res.reason or "KC_MIDDLE" in gate_res.reason


def test_v_turn_short_signal_blocked_at_execution_gate():
    async def _run():
        account = MockTestnetAccount()
        bars = [
            dict(timestamp=60_000, open=0.004250, high=0.004265, low=0.004245, close=0.004260,
                 atr=0.000050, ma3=0.004255, ma5=0.004252, ma15=0.004250,
                 kc_upper=0.004350, kc_middle=0.004270, kc_lower=0.004190, is_closed=True),
            dict(timestamp=120_000, open=0.004260, high=0.004285, low=0.004258, close=0.004280,
                 atr=0.000050, ma3=0.004275, ma5=0.004268, ma15=0.004255,
                 kc_upper=0.004350, kc_middle=0.004270, kc_lower=0.004190, is_closed=True),
        ]
        df = pd.DataFrame(bars)
        df.attrs["timeframe_ms"] = 60_000.0

        payload = {
            "entry_type": "SWING_PIVOT",
            "candidate_signal_type": "SWING_PIVOT_SHORT",
            "initial_sl": 0.004300,
            "entry_gate_frame": df,
            "source": "UNIFIED_PIPELINE",
            "upstream_entry_gate_authorized": True,
        }

        allowed, reason = await validate_entry_at_execution(
            account=account,
            symbol="1000PEPEUSDT",
            side="SHORT",
            context=payload,
            execution_price=0.004280,
        )

        assert allowed is False
        assert "DIRECTION_LOCK" in reason

    asyncio.run(_run())


def test_meme_coin_breakout_extension_relaxed_pass():
    # 測試妖幣破軌暴衝：價格距外軌 1.5 ATR (原 0.5 ATR 會被殺，現在 2.5~3.5 ATR 放寬通過)
    atr_val = 0.000010
    kc_up = 0.004280
    price_extended = kc_up + 1.5 * atr_val  # 距外軌 1.5 ATR
    # 提供足夠的歷史 K 棒（>= 20 根），使最後一根明確創出 20 根新高突破唐奇安箱體
    bars = []
    base_price = 0.004200
    for i in range(25):
        ts = (i + 1) * 60_000
        p = base_price + i * 0.000002
        bars.append(
            dict(timestamp=ts, open=p - 0.000001, high=p + 0.000001, low=p - 0.000001, close=p,
                 atr=atr_val, ma3=p - 0.000001, ma5=p - 0.000002, ma15=p - 0.000004,
                 kc_upper=p + 0.000015, kc_middle=p, kc_lower=p - 0.000015,
                 is_closed=True, volume=1000, volume_avg=800)
        )
    # 最後一根價格暴衝，距外軌 1.5 ATR 且大幅突破 Donchian Box
    kc_up = bars[-1]["kc_upper"]
    price_extended = kc_up + 1.5 * atr_val
    bars.append(
        dict(timestamp=26 * 60_000, open=bars[-1]["close"], high=price_extended + 0.000002,
             low=bars[-1]["close"], close=price_extended,
             atr=atr_val, ma3=price_extended - 0.000002, ma5=price_extended - 0.000005, ma15=price_extended - 0.000020,
             kc_upper=kc_up + 0.000005, kc_middle=bars[-1]["kc_middle"] + 0.000002, kc_lower=kc_up - 0.000035,
             is_closed=True, volume=3000, volume_avg=800)
    )
    df = pd.DataFrame(bars)
    df.attrs["timeframe_ms"] = 60_000.0
    df.attrs["macro_trend"] = "BULLISH"

    gate_res = CentralEntryGate.verify_and_authorize(
        symbol="1000PEPEUSDT",
        side="LONG",
        price=price_extended,
        klines_or_snapshot=df,
        source="UNIFIED_PIPELINE CONTINUATION_OUTSIDE_KC_LONG",
    )

    # 驗證不再被 VETO_KC_OUTER_OVEREXTENSION 阻斷
    assert gate_res.passed is True, f"Meme coin breakout extension should pass, got: {gate_res.reason}"

