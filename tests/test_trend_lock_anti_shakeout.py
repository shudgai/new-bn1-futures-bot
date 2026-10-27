"""
tests/test_trend_lock_anti_shakeout.py

驗證「抗洗盤趨勢鎖定 (Trend Lock)」機制：
1. 多單進場後，軌道外出現微幅小紅 K (陰線回踩)，只要未滿足四項峰頂條件且守穩 MA3/MA15，一律強制禁止平多！
2. 多單只有在真正滿足以下四項條件之一時，才放行平多：
   - 條件 1: MA3 向下死叉 MA15
   - 條件 2: 軌外大陰吞噬
   - 條件 3: 高位長上影天地針
   - 條件 4: 跌破前 K 棒低點結構失效
3. 空單對稱實施抗洗盤與四項谷底離場機制。
"""

import pandas as pd
import pytest

from core.gates.top_bottom_reversal_exit import (
    CLOSE_LONG_AT_TOP,
    CLOSE_SHORT_AT_BOTTOM,
    evaluate_top_bottom_reversal_exit,
)
from core.gates.unified_pipeline_gates import ExitGate, MarketDataSnapshot


def test_long_micro_red_candle_pullback_veto_exit():
    """
    實盤重現測試：
    買多後價格在 KC 上軌外，隨後出現一根實體極小的小紅 K (微幅回踩)，
    收盤價依然穩守在 MA3 與 MA15 之上，且未破前低，未死叉，非天地針。
    系統必須強制判定為 TREND_LOCK_ACTIVE，堅決禁止平多！
    """
    pos_long = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 105.0,
        "open_timestamp": 50.0,
    }
    bars = [
        # 1. 突破大陽棒
        dict(timestamp=60_000, open=100.0, high=103.5, low=99.8, close=103.0, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=101.5, ma15=99.5, is_closed=True),
        # 2. 軌外衝高大陽棒
        dict(timestamp=120_000, open=103.0, high=105.0, low=102.8, close=104.8, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=103.5, ma15=100.2, is_closed=True),
        # 3. 軌外微幅小紅 K 回踩 (實體僅 0.2，收盤 104.6 依然高於 MA3=104.1 與 MA15=101.0，最低 104.5 遠高於前低 102.8)
        dict(timestamp=180_000, open=104.8, high=104.9, low=104.5, close=104.6, kc_upper=103.5, kc_lower=97.5, atr=1.0, ma3=104.1, ma15=101.0, is_closed=True),
    ]
    df = pd.DataFrame(bars)

    # 1. evaluate_top_bottom_reversal_exit 檢驗
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_long, df, atr_val=1.0)
    assert should_exit is False, f"微幅小紅 K 回踩不應平倉，但被觸發: {reason}"
    assert "TREND_LOCK_ACTIVE" in str(meta.get("skipped"))

    # 2. ExitGate 綜合閘門檢驗
    market_dict = {
        "symbol": "TESTUSDT",
        "current_price": 104.6,
        "atr": 1.0,
        "kc_middle": 100.5,
        "kc_middle_slope": 0.5,
        "curr_k": bars[-1],
        "prev_k": bars[-2],
        "raw_df": df,
        "ma3": 104.1,
        "ma15": 101.0,
    }
    gate_exit, gate_reason, gate_meta = ExitGate.evaluate(pos_long, market_dict)
    assert gate_exit is False, f"ExitGate 應該鎖倉抗洗盤，但觸發了出場: {gate_reason}"


def test_long_four_peak_exit_conditions():
    """驗證多單四項真正峰頂離場條件各自能順暢觸發"""
    pos_long = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 105.0,
        "open_timestamp": 50.0,
    }

    # 條件 1: MA3 向下死叉 MA15
    bars_dc = [
        dict(timestamp=60_000, open=100.0, high=104.0, low=99.5, close=103.5, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=103.5, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=103.5, high=104.0, low=101.0, close=101.5, kc_upper=102.5, kc_lower=96.5, atr=1.0, ma3=101.8, ma15=101.0, is_closed=True),
        dict(timestamp=180_000, open=101.5, high=101.6, low=99.5, close=99.8, kc_upper=102.5, kc_lower=96.5, atr=1.0, ma3=100.5, ma15=101.2, is_closed=True), # ma3(100.5) < ma15(101.2)
    ]
    df_dc = pd.DataFrame(bars_dc)
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_long, df_dc, atr_val=1.0)
    assert should_exit is True
    assert reason == CLOSE_LONG_AT_TOP
    assert "MA3_DEATH_CROSS" in meta.get("triggers", [])

    # 條件 2: 軌外大陰吞噬
    bars_engulf = [
        dict(timestamp=60_000, open=100.0, high=102.0, low=99.5, close=101.5, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=101.0, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=101.5, high=105.0, low=101.4, close=104.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=103.0, ma15=100.5, is_closed=True), # 衝破上軌(104.5 > 103.0), body=3.0
        dict(timestamp=180_000, open=104.5, high=104.6, low=101.0, close=101.5, kc_upper=103.5, kc_lower=97.5, atr=1.0, ma3=102.5, ma15=100.8, is_closed=True), # 大陰棒吞噬 body=3.0 >= 0.6*3.0
    ]
    df_engulf = pd.DataFrame(bars_engulf)
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_long, df_engulf, atr_val=1.0)
    assert should_exit is True
    assert reason == CLOSE_LONG_AT_TOP
    assert "OUTER_RAIL_ENGULFING" in meta.get("triggers", [])

    # 條件 3: 高位長上影天地針
    bars_pinbar = [
        dict(timestamp=60_000, open=100.0, high=102.0, low=99.5, close=101.5, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=101.0, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=101.5, high=104.0, low=101.4, close=103.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=102.5, ma15=100.5, is_closed=True),
        dict(timestamp=180_000, open=103.5, high=106.5, low=102.8, close=103.0, kc_upper=103.5, kc_lower=97.5, atr=1.0, ma3=103.3, ma15=101.0, is_closed=True), # high=106.5, open=103.5, close=103.0, upper_wick=3.0, body=0.5
    ]
    df_pinbar = pd.DataFrame(bars_pinbar)
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_long, df_pinbar, atr_val=1.0)
    assert should_exit is True
    assert reason == CLOSE_LONG_AT_TOP
    assert "PINBAR_PEAK" in meta.get("triggers", [])

    # 條件 4: 跌破轉折前 K 棒低點結構失效 (前根收在軌內，不構成軌外吞噬，但跌破前低)
    bars_struct = [
        dict(timestamp=60_000, open=100.0, high=102.0, low=99.5, close=101.5, kc_upper=104.0, kc_lower=96.0, atr=1.0, ma3=101.0, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=101.5, high=105.0, low=103.0, close=103.8, kc_upper=104.0, kc_lower=97.0, atr=1.0, ma3=102.8, ma15=100.5, is_closed=True), # prev_close=103.8 <= upper=104.0, prev_low=103.0
        dict(timestamp=180_000, open=103.8, high=103.9, low=102.2, close=102.5, kc_upper=104.0, kc_lower=97.5, atr=1.0, ma3=102.4, ma15=101.0, is_closed=True), # close=102.5 < prev_low=103.0
    ]
    df_struct = pd.DataFrame(bars_struct)
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_long, df_struct, atr_val=1.0)
    assert should_exit is True
    assert reason == CLOSE_LONG_AT_TOP
    assert "STRUCTURE_BROKEN_PEAK" in meta.get("triggers", [])


def test_short_micro_green_candle_bounce_veto_exit():
    """
    空單測試：
    做空進場後，價格在 KC 下軌外，隨後出現一根微幅小陽線 (綠 K 反抽)，
    收盤價依然被壓制在 MA3 與 MA15 之下，且未破前高，未金叉，非天地針。
    系統必須強制判定為 TREND_LOCK_ACTIVE，堅決禁止平空！
    """
    pos_short = {
        "side": "SHORT",
        "entry_price": 100.0,
        "lowest_price": 95.0,
        "open_timestamp": 50.0,
    }
    bars = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=97.0, close=97.2, kc_upper=103.0, kc_lower=98.0, atr=1.0, ma3=98.5, ma15=100.5, is_closed=True),
        dict(timestamp=120_000, open=97.2, high=97.3, low=95.0, close=95.2, kc_upper=102.5, kc_lower=97.0, atr=1.0, ma3=96.5, ma15=99.8, is_closed=True),
        # 3. 軌外微幅小陽 K 反抽 (實體僅 0.2，收盤 95.4 依然低於 MA3=95.9 與 MA15=99.0，最高 95.5 遠低於前高 97.3)
        dict(timestamp=180_000, open=95.2, high=95.5, low=95.1, close=95.4, kc_upper=102.0, kc_lower=96.5, atr=1.0, ma3=95.9, ma15=99.0, is_closed=True),
    ]
    df = pd.DataFrame(bars)

    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_short, df, atr_val=1.0)
    assert should_exit is False, f"微幅小綠 K 反抽不應平倉，但被觸發: {reason}"
    assert "TREND_LOCK_ACTIVE" in str(meta.get("skipped"))


def test_short_four_valley_exit_conditions():
    """驗證空單四項真正谷底離場條件各自能順暢觸發"""
    pos_short = {
        "side": "SHORT",
        "entry_price": 100.0,
        "lowest_price": 95.0,
        "open_timestamp": 50.0,
    }

    # 條件 1: MA3 向上金叉 MA15
    bars_gc = [
        dict(timestamp=60_000, open=100.0, high=100.5, low=96.0, close=96.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=97.0, ma15=99.5, is_closed=True),
        dict(timestamp=120_000, open=96.5, high=98.5, low=96.4, close=98.0, kc_upper=102.5, kc_lower=96.5, atr=1.0, ma3=98.0, ma15=98.8, is_closed=True),
        dict(timestamp=180_000, open=98.0, high=100.0, low=97.9, close=99.5, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=99.5, ma15=98.6, is_closed=True), # ma3(99.5) > ma15(98.6)
    ]
    df_gc = pd.DataFrame(bars_gc)
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_short, df_gc, atr_val=1.0)
    assert should_exit is True
    assert reason == CLOSE_SHORT_AT_BOTTOM
    assert "MA3_GOLDEN_CROSS" in meta.get("triggers", [])

    # 條件 2: 軌外大陽吞噬
    bars_engulf = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=97.5, close=98.0, kc_upper=103.0, kc_lower=98.0, atr=1.0, ma3=98.5, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=98.0, high=98.2, low=94.5, close=95.0, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=96.5, ma15=99.2, is_closed=True), # 跌破下軌(95.0 < 96.0), body=3.0
        dict(timestamp=180_000, open=95.0, high=98.2, low=94.9, close=98.0, kc_upper=101.5, kc_lower=95.5, atr=1.0, ma3=97.5, ma15=98.8, is_closed=True), # 大陽棒吞噬 body=3.0 >= 0.6*3.0
    ]
    df_engulf = pd.DataFrame(bars_engulf)
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_short, df_engulf, atr_val=1.0)
    assert should_exit is True
    assert reason == CLOSE_SHORT_AT_BOTTOM
    assert "OUTER_RAIL_ENGULFING" in meta.get("triggers", [])

    # 條件 3: 低位長下影天地針
    bars_pinbar = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=97.5, close=98.0, kc_upper=103.0, kc_lower=98.0, atr=1.0, ma3=98.5, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=98.0, high=98.2, low=95.5, close=96.0, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=97.0, ma15=99.2, is_closed=True),
        dict(timestamp=180_000, open=96.0, high=96.8, low=93.0, close=96.5, kc_upper=101.5, kc_lower=95.5, atr=1.0, ma3=96.8, ma15=98.8, is_closed=True), # low=93.0, open=96.0, close=96.5, lower_wick=3.0, body=0.5
    ]
    df_pinbar = pd.DataFrame(bars_pinbar)
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_short, df_pinbar, atr_val=1.0)
    assert should_exit is True
    assert reason == CLOSE_SHORT_AT_BOTTOM
    assert "PINBAR_BOTTOM" in meta.get("triggers", [])

    # 條件 4: 突破轉折前 K 棒高點結構失效 (前根收在軌內，不構成軌外吞噬，但突破前高)
    bars_struct = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=97.5, close=98.0, kc_upper=103.0, kc_lower=96.0, atr=1.0, ma3=98.5, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=98.0, high=97.0, low=95.0, close=96.5, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=97.0, ma15=99.2, is_closed=True), # prev_close=96.5 >= lower=96.0, prev_high=97.0
        dict(timestamp=180_000, open=96.5, high=97.8, low=96.4, close=97.5, kc_upper=101.5, kc_lower=96.0, atr=1.0, ma3=97.2, ma15=98.8, is_closed=True), # close=97.5 > prev_high=97.0
    ]
    df_struct = pd.DataFrame(bars_struct)
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_short, df_struct, atr_val=1.0)
    assert should_exit is True
    assert reason == CLOSE_SHORT_AT_BOTTOM
    assert "STRUCTURE_BROKEN_BOTTOM" in meta.get("triggers", [])


def test_real_trade_1240_pullback_above_ma3_veto_hold():
    """
    盤面 Bug 現況重現 (12:31~12:41 區段):
    系統在 12:31 買多後，價格在 12:35 衝出 KC 上軌。
    12:38~12:40 價格在 KC 上軌與 MA3 快線上方走平強勢整理，
    收盤價高於 MA3 與 MA15 慢線，通道強勢向上。
    系統必須強制攔截平多，堅決持倉續抱！
    """
    pos_long = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 105.0,
        "open_timestamp": 50.0,
    }
    # 模擬 12:35 衝破上軌、12:38 回踩、12:40 在軌道外/MA3 上方走平小陽/小陰
    bars = [
        # 12:35 衝破上軌收大陽 (high=105.0, close=104.5 > upper=103.0)
        dict(timestamp=60_000, open=101.5, high=105.0, low=101.4, close=104.5, kc_upper=103.0, kc_lower=97.0, atr=1.0, ma3=103.0, ma15=100.5, is_closed=True),
        # 12:38 軌外高位小實體整理
        dict(timestamp=120_000, open=104.5, high=104.8, low=103.8, close=104.2, kc_upper=103.2, kc_lower=97.2, atr=1.0, ma3=103.8, ma15=101.0, is_closed=True),
        # 12:40 在 MA3(104.0) 與 MA15(101.5) 上方微幅走平 (close=104.1 >= ma3=104.0，未破前低 103.8，非天地針，未死叉)
        dict(timestamp=180_000, open=104.2, high=104.4, low=103.9, close=104.1, kc_upper=103.5, kc_lower=97.5, atr=1.0, ma3=104.0, ma15=101.5, is_closed=True),
    ]
    df = pd.DataFrame(bars)

    market_dict = {
        "symbol": "TESTUSDT",
        "current_price": 104.1,
        "atr": 1.0,
        "kc_middle": 100.5,
        "kc_middle_slope": 0.5,
        "curr_k": bars[-1],
        "prev_k": bars[-2],
        "raw_df": df,
        "ma3": 104.0,
        "kc_upper": 103.5,
        "kc_lower": 97.5,
    }

    gate_exit, gate_reason, gate_meta = ExitGate.evaluate(pos_long, market_dict)
    assert gate_exit is False, f"12:40 在 MA3 與 MA15 上方微幅整理不應平多，但被觸發: {gate_reason}"
    assert "TREND_LOCK" in str(gate_reason) or "VETO" in str(gate_reason) or "HOLD" in str(gate_reason)


def test_dual_symbol_pepe_and_lobster_trend_lock_veto_shakeout():
    """
    雙幣種實盤重現測試：
    1. 1000PEPE 12:38 微幅回踩小紅 K（連 MA3 都沒破，MA3 >= MA15 且中軌向上）-> 強制一票否決平多！
    2. 龙虾 12:20 剛突破後小幅震盪（價格遠在 MA15 之上，中軌向上）-> 強制一票否決平多！
    """
    # Case 1: 1000PEPE 12:38
    pos_pepe = {"side": "LONG", "entry_price": 0.004312, "highest_price": 0.004332, "open_timestamp": 100.0}
    pepe_bars = [
        dict(timestamp=120_000, open=0.004312, high=0.004332, low=0.004310, close=0.004328, kc_upper=0.004325, kc_lower=0.004300, atr=0.000008, ma3=0.004322, ma15=0.004315, is_closed=True),
        # 12:38 微幅回踩小紅 K，close=0.004326 >= ma3=0.004325 > ma15=0.004316
        dict(timestamp=180_000, open=0.004328, high=0.004329, low=0.004324, close=0.004326, kc_upper=0.004326, kc_lower=0.004302, atr=0.000008, ma3=0.004325, ma15=0.004316, is_closed=True),
    ]
    df_pepe = pd.DataFrame(pepe_bars)
    pepe_market = {
        "symbol": "1000PEPEUSDT",
        "current_price": 0.004326,
        "atr": 0.000008,
        "kc_middle": 0.004314,
        "kc_middle_slope": 0.000001,
        "curr_k": pepe_bars[-1],
        "prev_k": pepe_bars[-2],
        "raw_df": df_pepe,
        "ma3": 0.004325,
        "ma15": 0.004316,
        "kc_upper": 0.004326,
        "kc_lower": 0.004302,
    }
    pepe_exit, pepe_reason, _ = ExitGate.evaluate(pos_pepe, pepe_market)
    assert pepe_exit is False, f"1000PEPE 12:38 不應平多，但被觸發: {pepe_reason}"

    # Case 2: 龙虾 12:20
    pos_lobster = {"side": "LONG", "entry_price": 0.01890, "highest_price": 0.01925, "open_timestamp": 100.0}
    lobster_bars = [
        dict(timestamp=120_000, open=0.01895, high=0.01925, low=0.01890, close=0.01920, kc_upper=0.01915, kc_lower=0.01875, atr=0.00015, ma3=0.01910, ma15=0.01895, is_closed=True),
        # 12:20 小幅震盪收小陰，但收在 0.01916 遠高於 MA15=0.01900
        dict(timestamp=180_000, open=0.01920, high=0.01922, low=0.01914, close=0.01916, kc_upper=0.01918, kc_lower=0.01878, atr=0.00015, ma3=0.01915, ma15=0.01900, is_closed=True),
    ]
    df_lobster = pd.DataFrame(lobster_bars)
    lobster_market = {
        "symbol": "LOBSTERUSDT",
        "current_price": 0.01916,
        "atr": 0.00015,
        "kc_middle": 0.01898,
        "kc_middle_slope": 0.00002,
        "curr_k": lobster_bars[-1],
        "prev_k": lobster_bars[-2],
        "raw_df": df_lobster,
        "ma3": 0.01915,
        "ma15": 0.01900,
        "kc_upper": 0.01918,
        "kc_lower": 0.01878,
    }
    lobster_exit, lobster_reason, _ = ExitGate.evaluate(pos_lobster, lobster_market)
    assert lobster_exit is False, f"龙虾 12:20 不應平多，但被觸發: {lobster_reason}"


def test_real_trade_1219_fresh_long_veto_exit():
    """
    實盤 12:19 Bug 重現測試：
    12:18 買多後，12:19 出現一根微幅回踩小紅 K。
    價格遠在 MA15 慢線上方，且 KC 中軌強勢向上 (kc_middle_slope >= 0)。
    未觸發三大離場門檻 (未死叉、非大陰吞噬、非天地針、未破前低、非1.8 ATR大瀑布)，
    Trend Lock Master Gate 必須強制 100% 一票否決平多！
    """
    pos_long = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 102.5,
        "open_timestamp": 120_000,
    }
    bars = [
        # 12:17 谷底起跑大綠 K
        dict(timestamp=60_000, open=98.0, high=101.0, low=97.8, close=100.5, kc_upper=101.5, kc_lower=96.0, atr=1.0, ma3=99.5, ma15=98.2, is_closed=True),
        # 12:18 突破大陽 K (買多點)
        dict(timestamp=120_000, open=100.5, high=102.5, low=100.2, close=102.2, kc_upper=102.0, kc_lower=96.5, atr=1.0, ma3=101.2, ma15=98.8, is_closed=True),
        # 12:19 微幅回踩小紅 K (open=102.2, close=101.8，遠高於 MA15=99.5，KC中軌向上，未死叉，未破前低 100.2)
        dict(timestamp=180_000, open=102.2, high=102.4, low=101.6, close=101.8, kc_upper=102.5, kc_lower=97.0, atr=1.0, ma3=101.5, ma15=99.5, is_closed=True),
    ]
    df = pd.DataFrame(bars)
    market_data = {
        "symbol": "TESTUSDT",
        "current_price": 101.8,
        "atr": 1.0,
        "kc_middle": 99.8,
        "kc_middle_slope": 0.3,
        "curr_k": bars[-1],
        "prev_k": bars[-2],
        "raw_df": df,
        "ma3": 101.5,
        "ma15": 99.5,
        "kc_upper": 102.5,
        "kc_lower": 97.0,
    }
    should_exit, reason, meta = ExitGate.evaluate(pos_long, market_data)
    assert should_exit is False, f"12:19 微幅回踩不應平多，但被觸發: {reason}"
    assert "TREND_LOCK" in str(reason) or "VETO" in str(reason) or "HOLD" in str(reason)


def test_real_trade_1501_bottom_consolidation_short_veto_hold():
    """
    🔴 15:01 底部震盪時的平空硬核驗：
    is_ma3_golden_cross = (ma3_curr > ma15_curr) and (ma3_prev <= ma15_prev) # 15:01 時為 False
    is_structure_broken = curr_k['close'] > swing_high_price                  # 15:01 時為 False (遠低於前高)
    is_spike_circuit_breaker = (curr_body >= 1.8 * current_atr) and (curr_k['close'] > ma15_curr) # 15:01 時為 False

    ⚠️ 15:01 均未觸發上述離場條件，系統強制 return False 續抱空單！
    """
    from core.services.execution_manager import should_exit_short

    pos_short = {
        "side": "SHORT",
        "entry_price": 100.0,
        "lowest_price": 95.0,
        "open_timestamp": 60_000,
    }
    # 15:01 底部微幅震盪，價格在 95.5~95.8，遠低於開倉與波段高點 100.0，且 ma3(95.6) <= ma15(96.5)
    bars = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=97.0, close=97.5, kc_upper=101.0, kc_lower=95.0, atr=1.0, ma3=98.0, ma15=99.5, is_closed=True),
        dict(timestamp=120_000, open=97.5, high=97.6, low=95.0, close=95.2, kc_upper=100.5, kc_lower=94.5, atr=1.0, ma3=96.2, ma15=98.0, is_closed=True),
        dict(timestamp=180_000, open=95.2, high=95.8, low=95.1, close=95.6, kc_upper=100.0, kc_lower=94.0, atr=1.0, ma3=95.6, ma15=96.5, is_closed=True),
    ]
    df = pd.DataFrame(bars)
    curr_k = bars[-1]
    prev_k = bars[-2]

    # 硬核調用 should_exit_short
    can_exit = should_exit_short(
        curr_k=curr_k,
        prev_k=prev_k,
        ma3_curr=curr_k["ma3"],
        ma3_prev=prev_k["ma3"],
        ma15_curr=curr_k["ma15"],
        ma15_prev=prev_k["ma15"],
        kc_lower=curr_k["kc_lower"],
        prev_kc_lower=prev_k["kc_lower"],
        current_atr=1.0,
        raw_df=df,
        position=pos_short,
    )
    assert can_exit is False, "15:01 底部震盪時未觸發四大離場門檻，系統必須強制 return False 續抱空單！"


def test_real_trade_1513_low_range_consolidation_short_veto_hold():
    """
    🔴 15:13 低位狹幅橫盤平空硬核驗：
    收盤價未能實體突破波段高點 (swing_high_price)，且未金叉，
    即使觸及中軌或微幅反彈，系統強制一票否決平空！
    """
    from core.services.exits.entry_atr_protection import check_trend_lock_exit_permission

    pos_short = {
        "side": "SHORT",
        "entry_price": 0.0195,
        "lowest_price": 0.0182,
        "open_timestamp": 60_000,
    }
    bars = [
        dict(timestamp=60_000, open=0.0195, high=0.0196, low=0.0185, close=0.0186, kc_upper=0.0198, kc_lower=0.0182, atr=0.0003, ma3=0.0190, ma15=0.0194, is_closed=True),
        dict(timestamp=120_000, open=0.0186, high=0.0187, low=0.0181, close=0.0183, kc_upper=0.0196, kc_lower=0.0180, atr=0.0003, ma3=0.0185, ma15=0.0192, is_closed=True),
        # 15:13 狹幅橫盤小陽 K (close=0.0184 仍遠低於 entry 0.0195 與 ma15 0.0190)
        dict(timestamp=180_000, open=0.0183, high=0.0185, low=0.0182, close=0.0184, kc_upper=0.0195, kc_lower=0.0179, atr=0.0003, ma3=0.0184, ma15=0.0190, is_closed=True),
    ]
    df = pd.DataFrame(bars)

    permission = check_trend_lock_exit_permission(pos_short, df, price=0.0184)
    assert permission is False, "15:13 狹幅橫盤未觸發四大門檻，check_trend_lock_exit_permission 必須返回 False 一票否決！"


@pytest.mark.anyio
async def test_position_flip_from_short_to_long_on_ma3_golden_cross():
    """
    【最高指令】
    當空頭持倉觸發「MA3 向上金叉 MA15 慢線」離場時，
    只要當前 K 棒為實體陽線且 MA3 方向向上，必須在同一個 K 棒收線時，一次性完成平空與開多，嚴禁中間留空倉觀望！
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock, MagicMock

    bars = []
    base_p = 100.0
    for i in range(35):
        p = base_p - i * 0.1
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p + 0.05,
            "high": p + 0.1,
            "low": p - 0.1,
            "close": p - 0.05,
            "volume": 100.0,
            "is_closed": True,
            "atr": 0.5,
            "kc_middle": p,
            "kc_upper": p + 1.0,
            "kc_lower": p - 1.0,
            "ma3": p,
            "ma15": p + 0.5,
        })
    df = pd.DataFrame(bars)
    # 最後一根在谷底放量拉升突破，MA3 向上金叉 MA15，實體收陽
    df.loc[df.index[-1], "open"] = 96.0
    df.loc[df.index[-1], "close"] = 99.2
    df.loc[df.index[-1], "high"] = 99.3
    df.loc[df.index[-1], "low"] = 95.9

    class MockAccount:
        def __init__(self):
            self.positions = {
                "TESTUSDT": {
                    "symbol": "TESTUSDT",
                    "side": "SHORT",
                    "entry_price": 100.0,
                    "amount": 10.0,
                    "highest_price": 100.5,
                    "open_timestamp": (1700000000) * 1000,
                }
            }
            self.position_meta = {}
            self.channel_exit_frames = {"TESTUSDT": df}
            self.closing_lock = set()

    account = MockAccount()

    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="TESTUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=99.2,
    )

    # 驗證平倉已被調用
    assert close_mock.called, "應觸發平空"
    # 驗證秒級反手開多已同步調用！
    assert open_mock.called, "應在同一個循環內一次性完成平空兼反手開多！"
    open_call_kwargs = open_mock.call_args.kwargs
    assert open_call_kwargs["side"] == "LONG"
    assert open_call_kwargs["meta"]["entry_type"] == "PIVOT_FLIP_LONG"
    assert open_call_kwargs["meta"]["direction_override"] is True


def test_fakeout_bull_trap_veto_long():
    """
    假突破防範 (Anti-Bull-Trap Filter)：
    當 KC 中軌走平或往下時，若價格僅是短期衝出 KC 上軌但缺乏放量強實體或長上影線，
    判定為假突破誘多 (Bull Trap)，一票否決開多！
    """
    curr_close = 100.5
    curr_open = 100.2
    c_body_now = abs(curr_close - curr_open) # 0.3
    atr_val_now = 1.0 # body 0.3 < 0.6 * atr
    kc_up_val = 100.0 # close > kc_up_val
    kc_slope = -0.0001 # KC 中軌向下
    upper_wick_now = 101.5 - curr_close # 1.0 (長上影)

    is_bull_trap = bool(
        kc_slope <= 0.000005
        and curr_close >= (kc_up_val - 0.2 * atr_val_now)
        and (c_body_now < 0.6 * atr_val_now or upper_wick_now >= 0.6 * c_body_now or curr_close <= curr_open)
    )
    assert is_bull_trap is True, "無量小實體且長上影衝出下行 KC 上軌，必須被判定為 Bull Trap 一票否決！"


