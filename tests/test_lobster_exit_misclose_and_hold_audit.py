"""
tests/test_lobster_exit_misclose_and_hold_audit.py
==================================================
龍蝦 Binance Futures Bot 2.0：修正回踩誤平倉與應平未平倉之專屬回歸驗證套件

涵蓋十大情境：
1. 強勢同向趨勢中，單根小陰線或普通影線回踩，不應無故全平 (續抱)。
2. 正常回踩 EMA5/MA15，且結構完好時，強制 VETO_HOLD 續抱。
3. 已確認的結構破壞 (收盤貫穿中軌 > 0.3 ATR 且跌破成本、大週期衰退) 時，必須及時退出。
4. 帳戶級硬止損 (-3.0 ATR) 與極端瀑布觸發時，絕不被任何 VETO 攔截。
5. 三點樞紐在未達獲利展開標準時 (微利雜訊) 不得提早全平。
6. 三點樞紐在波段獲利展開並確認見頂時，確實產生退出決策。
7. 退出訊號原因碼與方向一致。
8. 同一根 K 線重複評估具備確定性與冪等性。
9. 反側 KC 突破在大週期同向時，毛刺不平倉，收線確認深破時必平倉。
10. Mock 呼叫鏈測試：確認 ExitGate 的 should_exit 會正確被 ExecutionManager 呼叫 close_position。
"""

import pytest
import pandas as pd
import numpy as np
import asyncio
from unittest.mock import AsyncMock, patch

from core.gates.unified_pipeline_gates import ExitGate
from core.services.data_pipeline import MarketDataSnapshot, DataPipeline
from core.services.execution_manager import ExecutionManager


def _create_snapshot(
    symbol="LOBSTERUSDT",
    quote=0.0210,
    side="LONG",
    macro_trend="BULLISH",
    kc_mid=0.0208,
    kc_up=0.0215,
    kc_low=0.0201,
    atr=0.0002,
    ma5=0.0211,
    ma5_prev=0.0210,
    ma15=0.0209,
    ema_fast=0.0209,
    ema_slow=0.0206,
    c_close=0.0210,
    c_open=0.0209,
    c_high=0.0212,
    c_low=0.0208,
    prev_high=0.0212,
    prev_low=0.0208,
    prev_close=0.0209,
    prev_open=0.0207,
):
    df_rows = [
        {"open": prev_open, "high": prev_high, "low": prev_low, "close": prev_close, "atr": atr, "kc_upper": kc_up, "kc_lower": kc_low, "kc_middle": kc_mid, "ma_slow": ema_slow, "bandwidth": 0.05},
        {"open": c_open, "high": c_high, "low": c_low, "close": c_close, "atr": atr, "kc_upper": kc_up, "kc_lower": kc_low, "kc_middle": kc_mid, "ma_slow": ema_slow, "bandwidth": 0.05},
    ]
    raw_df = pd.DataFrame(df_rows)

    curr_k = {"open": c_open, "high": c_high, "low": c_low, "close": c_close, "ma5": ma5, "ma15": ma15}
    prev_k = {"open": prev_open, "high": prev_high, "low": prev_low, "close": prev_close, "ma5": ma5_prev, "ma15": ma15}

    return MarketDataSnapshot(
        symbol=symbol,
        current_price=quote,
        curr_k=curr_k,
        prev_k=prev_k,
        ma5=ma5,
        ma15=ma15,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ma_spread_pct=1.0,
        fast_slope=0.00001,
        slow_slope=0.00001,
        kc_upper=kc_up,
        kc_middle=kc_mid,
        kc_lower=kc_low,
        kc_middle_slope=0.00001,
        atr=atr,
        peak_roe=0.0,
        min_price=c_low,
        max_price=c_high,
        macro_trend=macro_trend,
        macro_slope=0.00001,
        slow_slope_5=0.00001,
        bandwidth=0.05,
        raw_df=raw_df,
    )


def test_1_strong_trend_minor_pullback_strictly_holds():
    """情境 1：強勢多頭趨勢中，單根小陰線回踩，守穩 MA5 與中軌，強制 VETO_HOLD 續抱"""
    pos = {"side": "LONG", "entry_price": 0.0205, "leverage": 5.0, "peak_roe": 0.015}
    # 構造小陰線 (open=0.0211, close=0.02105)，但 MA5 依然向上，價格遠在 MA15 與 KC 中軌之上
    snap = _create_snapshot(quote=0.02105, c_open=0.0211, c_close=0.02105, ma5=0.0211, ma5_prev=0.0210, kc_mid=0.0208)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    assert should_exit is False, f"強勢多頭小陰線回踩不應平倉，實際原因: {reason}"
    assert reason == "VETO_HOLD"


def test_2_normal_pullback_to_ma5_ma15_holds():
    """情境 2：正常回踩 MA5/MA15 且趨勢結構完好時，判定健康回踩強制續抱"""
    pos = {"side": "LONG", "entry_price": 0.0207, "leverage": 5.0, "peak_roe": 0.01}
    # 回踩跌破 MA5 (0.02095 < ma5=0.0210)，但守穩在 MA15 (0.0209) 之上，MA5 斜率向上
    snap = _create_snapshot(quote=0.02095, c_open=0.0210, c_close=0.02095, ma5=0.0210, ma5_prev=0.0209, ma15=0.02085, kc_mid=0.02075)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    assert should_exit is False
    assert reason == "VETO_HOLD"
    cause = meta.get("cause", "")
    assert meta.get("healthy_pullback") is True or "多頭" in cause or "健康回踩" in cause or "MA5" in cause or "均線" in cause


def test_3_kc_mid_structure_break_exits_without_slow_ma_veto():
    """情境 3：實質收盤貫穿 KC 中軌 > 0.3 ATR 且跌破進場成本、大週期衰竭時，必須及時離場，不等慢線死叉"""
    pos = {"side": "LONG", "entry_price": 0.0210, "leverage": 5.0, "peak_roe": 0.005}
    # 價格大幅下砸至 0.0206 (跌破中軌 0.0208 超過 0.3*0.0002 = 0.02074)，跌破成本 0.0210，大週期轉為 SIDEWAYS/NEUTRAL
    snap = _create_snapshot(
        quote=0.0206, c_open=0.0209, c_close=0.0206,
        kc_mid=0.0208, atr=0.0002, macro_trend="NEUTRAL",
        ema_fast=0.0208, ema_slow=0.02075 # 慢線尚未死叉！
    )
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    assert should_exit is True, f"中軌結構破位且成本失守必須平多止損！實際原因: {reason}"
    assert reason in ("EXIT_BY_KC_MID_STRUCTURE_BROKEN", "LONG_EXIT_MA15_BREAK_AND_MA5_DECAY", "CIRCUIT_BREAKER_HARD_SL")


def test_4_hard_sl_and_waterfall_cannot_be_vetoed():
    """情境 4：-3.0 ATR 硬止損與巨型瀑布，絕對不可被任何 VETO_HOLD 攔截"""
    pos = {"side": "LONG", "entry_price": 0.0210, "leverage": 5.0, "peak_roe": 0.0}
    # 浮虧達到 -3.5 ATR (0.0210 - 3.5 * 0.0002 = 0.0203)
    snap = _create_snapshot(quote=0.0202, c_open=0.0208, c_close=0.0202, atr=0.0002)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    assert should_exit is True
    assert reason == "CIRCUIT_BREAKER_HARD_SL"

    # 巨型瀑布測試 (單根大陰棒貫穿中軌 >= 1.5 ATR)
    pos2 = {"side": "LONG", "entry_price": 0.0210, "leverage": 5.0, "peak_roe": 0.01}
    snap2 = _create_snapshot(
        quote=0.0207, c_open=0.0212, c_close=0.0207, kc_mid=0.02095, atr=0.0002
    )
    should_exit2, reason2, meta2 = ExitGate.evaluate(pos2, snap2)
    assert should_exit2 is True
    assert reason2 == "EXIT_BY_EXTREME_WATERFALL"


def test_5_pivot_without_gain_expansion_does_not_exit():
    """情境 5：剛開倉微漲 0.05% (ROE 0.25% < 0.6%)，遇到小陰線雜訊，不得提早全平"""
    pos = {"side": "LONG", "entry_price": 0.0210, "leverage": 5.0, "peak_roe": 0.0025} # 僅 0.25% ROE
    snap = _create_snapshot(
        quote=0.02101, c_open=0.02103, c_close=0.02101,
        prev_high=0.02104, c_high=0.02103, atr=0.0002
    )
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    assert should_exit is False, f"微利雜訊不應認定為真峰頂，實際原因: {reason}"
    assert reason == "VETO_HOLD"


def test_6_pivot_with_gain_expansion_exits_at_peak():
    """情境 6：波段獲利展開 (ROE >= 0.75%) 且已收線確立三點峰頂，及時平多鎖利"""
    pos = {"side": "LONG", "entry_price": 0.0210, "leverage": 5.0, "peak_roe": 0.012} # 1.2% ROE
    # 構造中間棒衝高 0.02130，確認棒收黑跌破中點 (open=0.02126, close=0.02116 < mid=0.02119)
    snap = _create_snapshot(
        quote=0.02116, c_open=0.02126, c_close=0.02116,
        prev_open=0.02110, prev_close=0.02128, prev_high=0.02130, prev_low=0.02110,
        ma5=0.02118, ma5_prev=0.02120,
        atr=0.0002
    )
    # 模擬 4 根已收線結構
    df_long = pd.DataFrame([
        {"open": 0.0210, "high": 0.0211, "low": 0.0209, "close": 0.0211, "atr": 0.0002, "kc_upper": 0.0212, "kc_lower": 0.0208, "kc_middle": 0.0210},
        {"open": 0.0211, "high": 0.0213, "low": 0.0211, "close": 0.02128, "atr": 0.0002, "kc_upper": 0.0212, "kc_lower": 0.0208, "kc_middle": 0.0210}, # 峰值棒 (中點 0.02119)
        {"open": 0.02126, "high": 0.02127, "low": 0.02115, "close": 0.02116, "atr": 0.0002, "kc_upper": 0.0212, "kc_lower": 0.0208, "kc_middle": 0.0210}, # 確認棒 (跌破中點收陰)
        {"open": 0.02116, "high": 0.02118, "low": 0.02114, "close": 0.02116, "atr": 0.0002, "kc_upper": 0.0212, "kc_lower": 0.0208, "kc_middle": 0.0210}, # 盤中棒
    ])
    snap.raw_df = df_long

    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is True
    assert reason == "EARLY_PIVOT_PEAK_EXIT"


def test_7_exit_direction_and_reason_consistency():
    """情境 7：多空方向與退出原因碼嚴格相符"""
    pos_long = {"side": "LONG", "entry_price": 0.0210, "leverage": 5.0}
    pos_short = {"side": "SHORT", "entry_price": 0.0210, "leverage": 5.0}

    # 多單硬止損
    snap_l = _create_snapshot(quote=0.0200, atr=0.0002)
    s_l, r_l, _ = ExitGate.evaluate(pos_long, snap_l)
    assert s_l is True
    assert "CIRCUIT_BREAKER_HARD_SL" in r_l

    # 空單硬止損
    snap_s = _create_snapshot(quote=0.0220, side="SHORT", atr=0.0002)
    s_s, r_s, _ = ExitGate.evaluate(pos_short, snap_s)
    assert s_s is True
    assert "CIRCUIT_BREAKER_HARD_SL" in r_s


def test_8_evaluation_idempotency_same_bar():
    """情境 8：同一根 K 線連續多次評估，回傳結果嚴格一致 (冪等性)"""
    pos = {"side": "LONG", "entry_price": 0.0205, "leverage": 5.0, "peak_roe": 0.01}
    snap = _create_snapshot(quote=0.0210)

    res1 = ExitGate.evaluate(pos, snap)
    res2 = ExitGate.evaluate(pos, snap)
    res3 = ExitGate.evaluate(pos, snap)

    assert res1[0] == res2[0] == res3[0]
    assert res1[1] == res2[1] == res3[1]


def test_9_opposite_kc_breach_intrabar_vs_closed_macro():
    """情境 9：反側 KC 突破在大週期同向時，盤中毛刺不平倉；已收線實質深破時必平倉"""
    pos = {"side": "LONG", "entry_price": 0.0210, "leverage": 5.0, "peak_roe": 0.005}
    # 盤中毛刺穿破下軌 (quote=0.02005 < kc_low=0.0201)，但大週期 BULLISH 且上一根已收線在軌內
    snap_spike = _create_snapshot(quote=0.02005, kc_low=0.0201, macro_trend="BULLISH", c_close=0.02015)
    should_exit_spike, reason_spike, meta_spike = ExitGate.evaluate(pos, snap_spike)
    # 盤中毛刺大週期同向不應觸發反側破軌平倉
    assert reason_spike != "OPPOSITE_KC_BAND_BREACH"

    # 若已收線收盤在下軌外深破 >= 0.5 ATR (0.0201 - 0.0001 = 0.0200)，且當前也在軌外
    snap_confirmed = _create_snapshot(quote=0.01995, kc_low=0.0201, macro_trend="BULLISH", c_close=0.01995, atr=0.0002)
    should_exit_conf, reason_conf, _ = ExitGate.evaluate(pos, snap_confirmed)
    assert should_exit_conf is True
    assert reason_conf in ("OPPOSITE_KC_BAND_BREACH", "EXIT_BY_KC_MID_STRUCTURE_BROKEN", "CIRCUIT_BREAKER_HARD_SL")


def test_10_execution_manager_receives_exit_signal_and_calls_close():
    """情境 10：Mock 測試執行呼叫鏈，確認 ExitGate 產生的退出訊號會被 ExecutionManager 傳送至 close_position"""
    from unittest.mock import MagicMock
    async def _run_async_test():
        mock_account = MagicMock()
        mock_position = {"symbol": "BTCUSDT", "side": "LONG", "entry_price": 80000.0, "qty": 0.1, "leverage": 5.0}
        mock_account.positions = {"BTCUSDT": mock_position}

        # 構造一個會觸發硬止損的快照
        snap = _create_snapshot(symbol="BTCUSDT", quote=75000.0, atr=500.0)

        with patch.object(DataPipeline, "calculate_snapshot", return_value=snap), \
             patch.object(ExecutionManager, "close_position", new_callable=AsyncMock) as mock_close:

            mock_close.return_value = True

            result = await ExecutionManager.process_symbol_pipeline(
                symbol="BTCUSDT",
                engine_or_account=mock_account,
                quote=75000.0,
                klines_df=pd.DataFrame([{"close": 75000.0}]),
            )

            assert result["status"] == "EXIT_EXECUTED"
            assert result["reason"] == "CIRCUIT_BREAKER_HARD_SL"
            mock_close.assert_awaited_once()

    asyncio.run(_run_async_test())
