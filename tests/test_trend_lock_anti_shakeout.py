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
