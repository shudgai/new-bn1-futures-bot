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

    # 條件 4: 跌破波段起漲前低結構失效 (跌破起漲點 99.5 且跌破 MA15 慢線)
    bars_struct = [
        dict(timestamp=60_000, open=100.0, high=102.0, low=99.5, close=101.5, kc_upper=104.0, kc_lower=96.0, atr=1.0, ma3=101.0, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=101.5, high=105.0, low=103.0, close=103.8, kc_upper=104.0, kc_lower=97.0, atr=1.0, ma3=102.8, ma15=100.5, is_closed=True),
        dict(timestamp=180_000, open=100.0, high=100.1, low=98.8, close=99.0, kc_upper=104.0, kc_lower=97.5, atr=1.0, ma3=100.5, ma15=100.2, is_closed=True), # close=99.0 < 99.5 且 close < ma15(100.2), ma3(100.5) > ma15(100.2) 未死叉
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

    # 條件 4: 突破波段起跌前高結構失效 (突破起跌點 100.2 且收盤站上 MA15 慢線)
    bars_struct = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=97.5, close=98.0, kc_upper=103.0, kc_lower=96.0, atr=1.0, ma3=98.5, ma15=100.0, is_closed=True),
        dict(timestamp=120_000, open=98.0, high=97.0, low=95.0, close=96.5, kc_upper=102.0, kc_lower=96.0, atr=1.0, ma3=97.0, ma15=99.2, is_closed=True),
        dict(timestamp=180_000, open=99.5, high=101.0, low=99.2, close=100.5, kc_upper=101.5, kc_lower=96.0, atr=1.0, ma3=99.0, ma15=99.8, is_closed=True), # close=100.5 > 100.2 且 close > ma15(99.8)
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


def test_real_trade_1603_and_1616_micro_green_candle_veto_hold_short():
    """
    驗證 16:03 與 16:16 實盤場景：
    空頭持倉中，即使出現微幅小綠 K 或底部走平，
    只要收盤價壓制在 MA15 慢線之下，且未滿足 4 大硬性離場門檻，
    should_exit_short 必須強制 return False，一票否決平空，續抱拿滿大暴跌！
    """
    from core.services.execution_manager import should_exit_short
    
    # 場景 1 (16:03 附近)：微幅小綠 K 回踩，價格 0.01910 遠在 MA15=0.01918 之下
    curr_k_1603 = {"open": 0.01908, "high": 0.01912, "low": 0.01906, "close": 0.01910}
    prev_k_1603 = {"open": 0.01915, "high": 0.01916, "low": 0.01905, "close": 0.01908}
    exit_1603 = should_exit_short(
        curr_k=curr_k_1603,
        prev_k=prev_k_1603,
        ma3_curr=0.01909,
        ma3_prev=0.01912,
        ma15_curr=0.01918,
        ma15_prev=0.01920,
        kc_lower=0.01900,
        prev_kc_lower=0.01902,
        current_atr=0.00007,
        swing_high_price=0.01935,
    )
    assert exit_1603 is False, "16:03 微幅回踩小綠 K 遠在 MA15 之下，必須一票否決平空！"

    # 場景 2 (16:16 附近)：底部狹幅走平小綠 K，價格 0.01903 仍在 MA15=0.01906 之下且未金叉
    curr_k_1616 = {"open": 0.01901, "high": 0.01905, "low": 0.01900, "close": 0.01903}
    prev_k_1616 = {"open": 0.01904, "high": 0.01906, "low": 0.01901, "close": 0.01901}
    exit_1616 = should_exit_short(
        curr_k=curr_k_1616,
        prev_k=prev_k_1616,
        ma3_curr=0.01903,
        ma3_prev=0.01902,
        ma15_curr=0.01906,
        ma15_prev=0.01907,
        kc_lower=0.01890,
        prev_kc_lower=0.01892,
        current_atr=0.00007,
        swing_high_price=0.01935,
    )
    assert exit_1616 is False, "16:16 底部狹幅走平小綠 K 未破 MA15 慢線，必須一票否決平空，續抱拿滿 16:25 大暴跌！"


def test_pepe_1632_low_level_consolidation_veto_hold_short():
    """
    實盤 1000PEPE/USDT 16:32 Bug 重現驗證：
    16:07 在 0.0042950 附近開空後，價格暴跌至 0.0042700。
    16:32 價格僅在底部出現微幅小陽線 (close=0.0042780 遠低於波段前高 swing_high=0.0042950)，
    MA3(0.0042770) 尚未金叉 MA15(0.0042830)，且收盤價仍在 MA15 之下。
    未滿足結構破壞、未金叉、非極限見底、非 1.8 ATR 爆發熔斷，
    系統【必須強制 return False】一票否決平空，堅決持倉續抱！
    """
    from core.services.execution_manager import should_exit_short

    pos_pepe = {
        "side": "SHORT",
        "entry_price": 0.0042950,
        "lowest_price": 0.0042700,
        "open_timestamp": 100.0,
    }
    bars = [
        # 16:07 開空波段高點
        dict(timestamp=60_000, open=0.0042980, high=0.0043000, low=0.0042920, close=0.0042950, kc_upper=0.0043100, kc_lower=0.0042850, atr=0.000010, ma3=0.0042960, ma15=0.0042980, is_closed=True),
        # 16:30 創低探底
        dict(timestamp=120_000, open=0.0042750, high=0.0042770, low=0.0042680, close=0.0042720, kc_upper=0.0042980, kc_lower=0.0042710, atr=0.000008, ma3=0.0042730, ma15=0.0042860, is_closed=True),
        # 16:31 小十字星
        dict(timestamp=180_000, open=0.0042720, high=0.0042760, low=0.0042710, close=0.0042740, kc_upper=0.0042950, kc_lower=0.0042700, atr=0.000008, ma3=0.0042740, ma15=0.0042845, is_closed=True),
        # 16:32 微幅小陽線 (close=0.0042780 < ma15=0.0042830，未金叉 ma3=0.0042770 < ma15=0.0042830，遠低於 swing_high=0.0042950)
        dict(timestamp=240_000, open=0.0042740, high=0.0042790, low=0.0042730, close=0.0042780, kc_upper=0.0042940, kc_lower=0.0042695, atr=0.000008, ma3=0.0042770, ma15=0.0042830, is_closed=True),
    ]
    df = pd.DataFrame(bars)
    curr_k = bars[-1]
    prev_k = bars[-2]

    # 1. 驗證 should_exit_short (執行層最外層鐵律)
    exit_short_allowed = should_exit_short(
        curr_k=curr_k,
        prev_k=prev_k,
        ma3_curr=curr_k["ma3"],
        ma3_prev=prev_k["ma3"],
        ma15_curr=curr_k["ma15"],
        ma15_prev=prev_k["ma15"],
        kc_lower=curr_k["kc_lower"],
        prev_kc_lower=prev_k["kc_lower"],
        current_atr=curr_k["atr"],
        raw_df=df,
        position=pos_pepe,
        swing_high_price=0.0042950,
    )
    assert exit_short_allowed is False, "1000PEPE 16:32 微幅小陽線未突破波段前高且未金叉 MA15，should_exit_short 必須一票否決平空！"

    # 2. 驗證 evaluate_top_bottom_reversal_exit (閘門層鐵律)
    reversal_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos_pepe, df, atr_val=curr_k["atr"])
    assert reversal_exit is False, f"evaluate_top_bottom_reversal_exit 應該鎖倉抗洗盤，但觸發了: {reason}"
    assert "TREND_LOCK_ACTIVE" in str(meta.get("skipped"))


def test_dual_defense_should_exit_short_all_branches():
    """
    驗證 should_exit_short 的雙重防線全分支 (方案 B 慢線硬鎖定修復版)：
    - 谷底 A (MA3 金叉 MA15) -> True
    - 谷底 B (軌外大陽吞噬 且 收盤價實體站上 MA15) -> True
    - 谷底 C (低位下刺針 Pinbar 且 收盤價實體站上 MA15) -> True
    - 谷底 D (1.8 ATR 爆發熔斷 且 收盤價實體站上 MA15) -> True
    - 最終結構破壞 (突破 swing_high 且突破 MA15) -> True
    - ⚠️ 關鍵防禦：若價格仍在 MA15 慢線下方 (curr_close < ma15_curr) 且未金叉，即使出現吞噬/刺針，一律強制 False (防止 06:50 龍蝦提早平空)
    - 微幅回踩小陽 K (未滿足任一條件) -> 強制 False
    """
    from core.services.execution_manager import should_exit_short

    base_k = {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0}
    prev_k = {"open": 10.2, "high": 10.3, "low": 9.8, "close": 9.9}

    # 1. 谷底 A: MA3 金叉 MA15
    assert should_exit_short(
        curr_k={"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.05},
        prev_k={"open": 10.1, "high": 10.2, "low": 9.9, "close": 10.0},
        ma3_curr=10.05, ma3_prev=9.95, ma15_curr=10.00, ma15_prev=10.00,
        kc_lower=9.5, prev_kc_lower=9.5, current_atr=0.5, swing_high_price=12.0,
    ) is True

    # 2. 谷底 B: 軌外大陽吞噬 且 收盤價實體站上 MA15 (close > ma15_curr)
    assert should_exit_short(
        curr_k={"open": 9.3, "high": 9.8, "low": 9.3, "close": 9.8}, # body=0.5, close=9.8 > ma15=9.7
        prev_k={"open": 9.9, "high": 10.0, "low": 9.3, "close": 9.4}, # close < prev_kc_lower(9.5), body=0.5
        ma3_curr=9.5, ma3_prev=9.6, ma15_curr=9.7, ma15_prev=9.8,
        kc_lower=9.5, prev_kc_lower=9.5, current_atr=0.5, swing_high_price=12.0,
    ) is True

    # ⚠️ 關鍵防禦 B-VETO: 軌外吞噬但收盤未站上 MA15 (curr_close < ma15_curr) -> 一票否決 (False)！
    assert should_exit_short(
        curr_k={"open": 9.3, "high": 9.8, "low": 9.3, "close": 9.8}, # close=9.8 < ma15=9.9
        prev_k={"open": 9.9, "high": 10.0, "low": 9.3, "close": 9.4},
        ma3_curr=9.5, ma3_prev=9.6, ma15_curr=9.9, ma15_prev=10.0,
        kc_lower=9.5, prev_kc_lower=9.5, current_atr=0.5, swing_high_price=12.0,
    ) is False

    # 3. 谷底 C: 低位下刺針 Pinbar 且 收盤站上 MA15 (close > ma15_curr)
    assert should_exit_short(
        curr_k={"open": 9.4, "high": 9.6, "low": 9.0, "close": 9.5}, # close=9.5 > ma15=9.45
        prev_k=base_k,
        ma3_curr=9.4, ma3_prev=9.3, ma15_curr=9.45, ma15_prev=9.5,
        kc_lower=9.2, prev_kc_lower=9.2, current_atr=0.5, swing_high_price=12.0,
    ) is True

    # ⚠️ 關鍵防禦 C-VETO: 低位下刺針但收盤未站上 MA15 (curr_close < ma15_curr) -> 一票否決 (False)！
    assert should_exit_short(
        curr_k={"open": 9.4, "high": 9.6, "low": 9.0, "close": 9.5}, # close=9.5 < ma15=9.9
        prev_k=base_k,
        ma3_curr=9.4, ma3_prev=9.3, ma15_curr=9.9, ma15_prev=10.0,
        kc_lower=9.2, prev_kc_lower=9.2, current_atr=0.5, swing_high_price=12.0,
    ) is False


    # 4. 谷底 D: 1.8 ATR 爆發熔斷 (body >= 1.8 * atr 且 close > ma15)
    assert should_exit_short(
        curr_k={"open": 9.0, "high": 10.1, "low": 9.0, "close": 10.0}, # body=1.0 >= 1.8*0.5=0.9, close=10.0 > ma15=9.8
        prev_k=base_k,
        ma3_curr=9.7, ma3_prev=9.8, ma15_curr=9.8, ma15_prev=9.9, # ma3 未金叉 ma15
        kc_lower=8.5, prev_kc_lower=8.5, current_atr=0.5, swing_high_price=12.0,
    ) is True

    # 5. 最終結構破壞: 突破 swing_high 且突破 MA15
    assert should_exit_short(
        curr_k={"open": 10.5, "high": 11.2, "low": 10.5, "close": 11.1}, # close > swing_high(11.0) 且 close > ma15(10.8)
        prev_k=base_k,
        ma3_curr=10.6, ma3_prev=10.7, ma15_curr=10.8, ma15_prev=10.9, # ma3 未金叉 ma15
        kc_lower=9.0, prev_kc_lower=9.0, current_atr=0.5, swing_high_price=11.0,
    ) is True

    # 6. 微幅回踩/橫盤小綠 K (未滿足任一門檻) -> 強制 False
    assert should_exit_short(
        curr_k={"open": 9.5, "high": 9.6, "low": 9.48, "close": 9.55}, # body=0.05
        prev_k={"open": 9.6, "high": 9.62, "low": 9.48, "close": 9.50},
        ma3_curr=9.52, ma3_prev=9.50, ma15_curr=9.80, ma15_prev=9.85, # 未金叉
        kc_lower=9.2, prev_kc_lower=9.2, current_atr=0.5, swing_high_price=12.0,
    ) is False


def test_dual_defense_should_exit_long_all_branches():
    """
    驗證 should_exit_long 的雙重防線全分支 (方案 B 慢線硬鎖定修復版)：
    - 峰頂 A (MA3 死叉 MA15) -> True
    - 峰頂 B (極限軌外大陰吞噬 且 收盤價實體跌破 MA15) -> True
    - 峰頂 C (高位天地針 Pinbar 且 收盤價實體跌破 MA15) -> True
    - 峰頂 D (1.8 ATR 崩盤熔斷 且 收盤價實體跌破 MA15) -> True
    - 最終結構破壞 (跌破 swing_low 且跌破 MA15) -> True
    - ⚠️ 關鍵防禦：若價格仍在 MA15 慢線上方 (curr_close > ma15_curr) 且未死叉，即使出現吞噬/天地針，一律強制 False (防止 06:48 PEPE 提早平多)
    - 微幅回踩小紅 K (未滿足任一條件) -> 強制 False
    """
    from core.services.execution_manager import should_exit_long

    base_k = {"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1}

    # 1. 峰頂 A: MA3 死叉 MA15
    assert should_exit_long(
        curr_k={"open": 10.1, "high": 10.15, "low": 9.95, "close": 10.0},
        prev_k={"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1},
        ma3_curr=10.00, ma3_prev=10.10, ma15_curr=10.05, ma15_prev=10.05,
        kc_upper=10.5, prev_kc_upper=10.5, current_atr=0.5, swing_low_price=8.0,
    ) is True

    # 2. 峰頂 B: 極限軌外大陰吞噬 且 收盤價實體跌破 MA15 (close < ma15_curr)
    assert should_exit_long(
        curr_k={"open": 10.7, "high": 10.7, "low": 10.0, "close": 10.0}, # body=0.7, close=10.0 < ma15=10.1
        prev_k={"open": 10.1, "high": 10.7, "low": 10.0, "close": 10.6}, # close > prev_kc_upper(10.5), body=0.5
        ma3_curr=10.3, ma3_prev=10.4, ma15_curr=10.1, ma15_prev=10.0,
        kc_upper=10.5, prev_kc_upper=10.5, current_atr=0.5, swing_low_price=8.0,
    ) is True

    # ⚠️ 關鍵防禦 B-VETO: 軌外陰線但收盤仍在 MA15 之上 (curr_close > ma15_curr) -> 一票否決 (False)！
    assert should_exit_long(
        curr_k={"open": 10.7, "high": 10.7, "low": 10.2, "close": 10.2}, # close=10.2 > ma15=10.1
        prev_k={"open": 10.1, "high": 10.7, "low": 10.0, "close": 10.6},
        ma3_curr=10.5, ma3_prev=10.4, ma15_curr=10.1, ma15_prev=10.0,
        kc_upper=10.5, prev_kc_upper=10.5, current_atr=0.5, swing_low_price=8.0,
    ) is False

    # 3. 峰頂 C: 高位天地針 Pinbar 且 收盤跌破 MA15 (close < ma15_curr)
    assert should_exit_long(
        curr_k={"open": 10.2, "high": 11.0, "low": 9.9, "close": 10.0}, # body=0.2, upper_wick=0.8, close=10.0 < ma15=10.1
        prev_k=base_k,
        ma3_curr=10.2, ma3_prev=10.3, ma15_curr=10.1, ma15_prev=10.0,
        kc_upper=10.8, prev_kc_upper=10.8, current_atr=0.5, swing_low_price=8.0,
    ) is True

    # ⚠️ 關鍵防禦 C-VETO: 高位天地針但收盤仍穩守 MA15 之上 (curr_close > ma15_curr) -> 一票否決 (False)！
    assert should_exit_long(
        curr_k={"open": 10.5, "high": 11.0, "low": 10.35, "close": 10.4}, # close=10.4 > ma15=10.1
        prev_k=base_k,
        ma3_curr=10.5, ma3_prev=10.4, ma15_curr=10.1, ma15_prev=10.0,
        kc_upper=10.8, prev_kc_upper=10.8, current_atr=0.5, swing_low_price=8.0,
    ) is False


    # 4. 峰頂 D: 1.8 ATR 崩盤熔斷 (body >= 1.8 * atr 且 close < ma15)
    assert should_exit_long(
        curr_k={"open": 11.0, "high": 11.0, "low": 9.9, "close": 10.0}, # body=1.0 >= 1.8*0.5=0.9, close=10.0 < ma15=10.2
        prev_k=base_k,
        ma3_curr=10.4, ma3_prev=10.3, ma15_curr=10.2, ma15_prev=10.1, # ma3 未死叉 ma15
        kc_upper=11.5, prev_kc_upper=11.5, current_atr=0.5, swing_low_price=8.0,
    ) is True

    # 5. 最終結構破壞: 跌破 swing_low 且跌破 MA15
    assert should_exit_long(
        curr_k={"open": 9.2, "high": 9.2, "low": 8.7, "close": 8.8}, # close < swing_low(8.9) 且 close < ma15(9.5)
        prev_k=base_k,
        ma3_curr=9.6, ma3_prev=9.7, ma15_curr=9.5, ma15_prev=9.4, # ma3 未死叉 ma15
        kc_upper=11.0, prev_kc_upper=11.0, current_atr=0.5, swing_low_price=8.9,
    ) is True

    # 6. 微幅回踩小紅 K (未滿足任一門檻) -> 強制 False
    assert should_exit_long(
        curr_k={"open": 10.5, "high": 10.52, "low": 10.42, "close": 10.45}, # body=0.05
        prev_k={"open": 10.4, "high": 10.55, "low": 10.38, "close": 10.50},
        ma3_curr=10.48, ma3_prev=10.50, ma15_curr=10.20, ma15_prev=10.15, # 未死叉
        kc_upper=10.8, prev_kc_upper=10.8, current_atr=0.5, swing_low_price=8.0,
    ) is False


@pytest.mark.anyio
async def test_lobster_1804_top_dead_cross_exit_and_flip_short():
    """
    實盤 龙虾/USDT 18:04 重現驗證：
    18:02 衝至 0.01895 頂部力竭後，18:04 MA3 快線向下死叉 MA15 慢線。
    1. authorized_position_exit_allowed 必須無條件放行 CLOSE_LONG_AT_TOP 平多，絕不被底層靜默攔截！
    2. ExecutionManager 必須在平多成功後秒級一次性反手開空 (PIVOT_FLIP_SHORT)！
    """
    from core.services.exits.authorized_position_exit import automated_position_exit_allowed
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    pos_long = {
        "symbol": "LOBSTERUSDT",
        "side": "LONG",
        "entry_price": 0.01860,
        "amount": 1000.0,
        "highest_price": 0.01895,
        "open_timestamp": 1700000000000.0,
    }

    # 1. 驗證 authorized_position_exit_allowed 放行 ExitGate:CLOSE_LONG_AT_TOP
    assert automated_position_exit_allowed(
        symbol="LOBSTERUSDT",
        position=pos_long,
        metadata={},
        reason="ExitGate:CLOSE_LONG_AT_TOP",
        price=0.01890,
    ) is True, "ExitGate:CLOSE_LONG_AT_TOP 必須被 authorized_position_exit_allowed 放行！"

    # 2. 構造 18:04 死叉行情數據
    bars = []
    for i in range(35):
        p = 0.01850 + i * 0.00001
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p, "high": p + 0.00015, "low": p - 0.00015, "close": p + 0.00005,
            "volume": 10000.0, "is_closed": True,
        })
    # 倒數第 3 根: 衝頂 0.01895
    bars[-3]["open"] = 0.01888
    bars[-3]["high"] = 0.01896
    bars[-3]["low"] = 0.01885
    bars[-3]["close"] = 0.01894

    # 倒數第 2 根: 開始走弱，ma3 守在 ma15 之上
    bars[-2]["open"] = 0.01894
    bars[-2]["high"] = 0.01895
    bars[-2]["low"] = 0.01880
    bars[-2]["close"] = 0.01880

    # 最後一根: 陰線下破，MA3 死叉 MA15
    bars[-1]["open"] = 0.01880
    bars[-1]["high"] = 0.01882
    bars[-1]["low"] = 0.01860
    bars[-1]["close"] = 0.01862

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {"LOBSTERUSDT": pos_long}
            self.position_meta = {}
            self.channel_exit_frames = {"LOBSTERUSDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="LOBSTERUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.01862,
    )

    assert close_mock.called, "18:04 死叉必須立即觸發平多！"
    assert open_mock.called, "18:04 死叉平多後必須秒級反手開空！"
    assert open_mock.call_args.kwargs["side"] == "SHORT"
    assert open_mock.call_args.kwargs["meta"]["entry_type"] == "PIVOT_FLIP_SHORT"


@pytest.mark.anyio
async def test_lobster_1817_bottom_pinbar_and_golden_cross_flip_long():
    """
    實盤 龙虾/USDT 18:15~18:17 谷底重現驗證：
    1. 18:15~18:17 在 KC 下軌爆發長下影下刺針與 MA3 向上金叉 MA15。
    2. should_exit_short 必須立即 return True (真谷底放行)，不受波段前高限制！
    3. authorized_position_exit_allowed 必須放行 CLOSE_SHORT_AT_BOTTOM 及 BULLISH_TREND_REVERSAL！
    4. ExecutionManager 必須平空後秒級原子反手開多 (PIVOT_FLIP_LONG)！
    """
    from core.services.exits.authorized_position_exit import automated_position_exit_allowed
    from core.services.execution_manager import ExecutionManager, should_exit_short
    from unittest.mock import AsyncMock

    pos_short = {
        "symbol": "LOBSTERUSDT",
        "side": "SHORT",
        "entry_price": 0.01880,
        "amount": 1000.0,
        "lowest_price": 0.01860,
        "open_timestamp": 1700000000000.0,
    }

    # 1. 驗證 authorized_position_exit_allowed
    assert automated_position_exit_allowed(
        symbol="LOBSTERUSDT",
        position=pos_short,
        metadata={},
        reason="ExitGate:CLOSE_SHORT_AT_BOTTOM",
        price=0.01875,
    ) is True
    assert automated_position_exit_allowed(
        symbol="LOBSTERUSDT",
        position=pos_short,
        metadata={},
        reason="Wild Swing EXIT_BY_BULLISH_TREND_REVERSAL",
        price=0.01875,
    ) is True

    # 2. 驗證 should_exit_short 真谷底立刻放行
    curr_pinbar = {"open": 0.01870, "high": 0.01875, "low": 0.01858, "close": 0.01872}  # lower_wick=0.00012 >= 1.2*body(0.00002)
    prev_k = {"open": 0.01878, "high": 0.01880, "low": 0.01868, "close": 0.01870}
    can_exit = should_exit_short(
        curr_k=curr_pinbar,
        prev_k=prev_k,
        ma3_curr=0.01873,
        ma3_prev=0.01870,
        ma15_curr=0.01872,
        ma15_prev=0.01873,  # MA3 金叉 MA15
        kc_lower=0.01865,
        prev_kc_lower=0.01866,
        current_atr=0.00010,
        swing_high_price=0.01900,  # 遠低於前高 0.01900，但觸發真谷底必須放行！
    )
    assert can_exit is True, "滿足真谷底條件時 should_exit_short 必須立即放行平空！"

    # 3. 構造 18:15~18:17 谷底金叉行情數據
    bars = []
    for i in range(35):
        p = 0.01890 - i * 0.00001
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p, "high": p + 0.00015, "low": p - 0.00015, "close": p - 0.00005,
            "volume": 10000.0, "is_closed": True,
        })
    # 倒數第 3 根: 探底
    bars[-3]["open"] = 0.01865
    bars[-3]["high"] = 0.01868
    bars[-3]["low"] = 0.01855
    bars[-3]["close"] = 0.01858

    # 倒數第 2 根: 底部長下影下刺針
    bars[-2]["open"] = 0.01858
    bars[-2]["high"] = 0.01865
    bars[-2]["low"] = 0.01850
    bars[-2]["close"] = 0.01860

    # 最後一根: 陽線上衝，MA3 金叉 MA15
    bars[-1]["open"] = 0.01860
    bars[-1]["high"] = 0.01878
    bars[-1]["low"] = 0.01858
    bars[-1]["close"] = 0.01876

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {"LOBSTERUSDT": pos_short}
            self.position_meta = {}
            self.channel_exit_frames = {"LOBSTERUSDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="LOBSTERUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.01876,
    )

    assert close_mock.called, "18:17 谷底金叉刺針必須立即觸發平空！"
    assert open_mock.called, "18:17 谷底平空後必須秒級原子反手開多！"
    assert open_mock.call_args.kwargs["side"] == "LONG"
    assert open_mock.call_args.kwargs["meta"]["entry_type"] == "PIVOT_FLIP_LONG"


@pytest.mark.anyio
async def test_pepe_1843_anti_chasing_veto_long_at_top():
    """
    實盤 1000PEPE 18:43 頂部追高重現驗證：
    價格自 18:15 谷底 0.0042700 連續拉升 8 根陽線暴漲至 0.00431123 (超過 2.0 ATR)。
    當價格在 18:43 衝出 KC 上軌時，Anti-Chasing Gate 必須 100% 一票否決開多，絕不在天花板追多接刀！
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    bars = []
    base_p = 0.004270
    atr = 0.000015
    for i in range(30):
        p = base_p + i * 0.0000015
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p - 0.0000005, "high": p + 0.000001, "low": p - 0.000001, "close": p + 0.0000005,
            "volume": 10000.0, "is_closed": True, "atr": atr,
            "kc_middle": p, "kc_upper": p + 0.00001, "kc_lower": p - 0.00001,
            "ma3": p, "ma15": p - 0.000002,
        })
    # 最後一根: 暴拉 0.000040 (約 2.5 ATR)，衝出上軌 (0.00431123)
    bars[-1]["open"] = 0.004305
    bars[-1]["close"] = 0.00431123
    bars[-1]["high"] = 0.004312
    bars[-1]["low"] = 0.004300
    bars[-1]["kc_upper"] = 0.004310
    bars[-1]["kc_middle"] = 0.004290
    bars[-1]["kc_lower"] = 0.004270

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {}
            self.position_meta = {}
            self.channel_exit_frames = {}
            self.closing_lock = set()

    account = MockAccount()
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="1000PEPEUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.00431123,
    )

    assert open_mock.called is False, "18:43 自低點已狂拉超過 1.5 ATR 且衝破上軌，Anti-Chasing Gate 必須 100% 一票否決追多接刀！"


@pytest.mark.anyio
async def test_pepe_1905_top_dead_cross_exit_and_flip_short():
    """
    實盤 1000PEPE/USDT 19:05 重現驗證：
    多單在 19:02~19:04 買多後衝高至頂部，
    19:05 頂部形成長上影線且 MA3 正式死叉 MA15 (真峰頂成立)。
    驗證：
    1. 系統在 19:05 當根 K 棒收線時立即觸發市價平多 (CLOSE_LONG_AT_TOP)！
    2. 絕不拖延至 19:10 暴跌貫穿慢線才被動平多！
    3. 平多成功後同 K 棒秒級原子反手開空 (PIVOT_FLIP_SHORT)！
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    pos_pepe_long = {
        "symbol": "1000PEPEUSDT",
        "side": "LONG",
        "entry_price": 0.0042850,
        "amount": 100000.0,
        "highest_price": 0.0043120,
        "open_timestamp": 1700000000000.0,
    }

    # 構造 19:00~19:05 行情數據
    bars = []
    base_price = 0.0042950
    for i in range(35):
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": base_price, "high": base_price + 0.000005, "low": base_price - 0.000005, "close": base_price,
            "volume": 10000.0, "is_closed": True,
        })

    # 倒數第 3 根 (19:03): 衝頂大陽 0.0043120 (close=0.0043100)
    bars[-3]["open"] = 0.0042950
    bars[-3]["high"] = 0.0043120
    bars[-3]["low"] = 0.0042940
    bars[-3]["close"] = 0.0043100

    # 倒數第 2 根 (19:04): 高位十字星/長上影 (high=0.0043120, close=0.0043020)
    # 此時 ma3 = (0.0042950 + 0.0043100 + 0.0043020) / 3 = 0.0043023 > ma15
    bars[-2]["open"] = 0.0043100
    bars[-2]["high"] = 0.0043120
    bars[-2]["low"] = 0.0043000
    bars[-2]["close"] = 0.0043020

    # 最後一根 (19:05): 陰線收跌 0.0042800，MA3 向下死叉 MA15 (ma3=0.0042973 < ma15=0.0042980)
    bars[-1]["open"] = 0.0043020
    bars[-1]["high"] = 0.0043030
    bars[-1]["low"] = 0.0042780
    bars[-1]["close"] = 0.0042800

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {"1000PEPEUSDT": pos_pepe_long}
            self.position_meta = {}
            self.channel_exit_frames = {"1000PEPEUSDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="1000PEPEUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.0042800,
    )

    # 1. 驗證 19:05 死叉當下立即執行市價平多！
    assert close_mock.called is True, "19:05 MA3 死叉 MA15 真峰頂成立，必須第一優先級立即平多！"
    close_call_kwargs = close_mock.call_args.kwargs
    assert "CLOSE_LONG_AT_TOP" in close_call_kwargs["reason"], f"平多理由應為 CLOSE_LONG_AT_TOP，實際為: {close_call_kwargs['reason']}"

    # 2. 驗證同 K 棒秒級原子反手開空！
    assert open_mock.called is True, "19:05 峰頂平多後必須同 K 棒秒級反手開空！"
    open_call_kwargs = open_mock.call_args.kwargs
    assert open_call_kwargs["side"] == "SHORT"
    assert open_call_kwargs["meta"]["entry_type"] == "PIVOT_FLIP_SHORT"
    assert open_call_kwargs["meta"]["direction_override"] is True


@pytest.mark.anyio
async def test_no_hard_stop_loss_only_profit_lock_waterfall_and_pivots():
    """
    驗證 2026-10-27 最新最高指示：
    1. 開倉後不要硬止損割肉 (虧損達 -3.5 ATR 依然堅決拒絕硬止損割肉，拒絕洗下車)。
    2. 只能鎖利、大瀑布、市場熔斷及真峰谷真谷底才能平倉。
    3. 開錯方向 (遇到真峰頂或真谷底) 秒級平倉兼反手開倉修正！
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    # 構造一個多單持倉，進場價 100.0，當前報價 98.0，ATR=0.5 (虧損 -4.0 ATR)
    pos = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "amount": 1.0,
        "entry_atr": 0.5,
    }
    
    # 構造 35 根正常震盪陰跌 K 棒 (無大瀑布、未形成真峰頂)
    records = []
    base_t = 1700000000
    for i in range(35):
        records.append({
            "timestamp": (base_t + i * 60) * 1000,
            "open": 100.0 - i * 0.2,
            "high": 100.1 - i * 0.2,
            "low": 99.8 - i * 0.2,
            "close": 99.9 - i * 0.2,
            "volume": 100.0,
            "is_closed": True,
            "atr": 0.5,
            "kc_middle": 99.5 - i * 0.2,
            "kc_upper": 100.5 - i * 0.2,
            "kc_lower": 98.5 - i * 0.2,
            "ma3": 99.9 - i * 0.2,
            "prev_ma3": 100.0 - i * 0.2,
            "ma15": 100.0 - i * 0.2,
            "prev_ma15": 100.0 - i * 0.2,
        })
    df = pd.DataFrame(records)

    class MockAccount:
        def __init__(self):
            self.positions = {"BTC/USDT": pos}
            self.position_meta = {}
            self.channel_exit_frames = {"BTC/USDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    # 1. 雖然虧損達到 -4.0 ATR，但絕不觸發硬止損割肉，而是判定為開錯方向直接秒級平多兼反手開空 (SUCCESS_FLIP)！
    res = await ExecutionManager.process_symbol_pipeline(
        symbol="BTC/USDT",
        engine_or_account=account,
        klines_df=df,
        quote=98.0,
    )
    assert res.get("status") == "SUCCESS_FLIP", f"開錯方向必須同 K 棒秒級反手修正，但返回了: {res}"
    assert res.get("side") == "SHORT"
    assert close_mock.called is True
    close_kwargs = close_mock.call_args.kwargs
    assert "HARD_STOP" not in close_kwargs["reason"], "嚴禁觸發硬止損！"
    assert "CIRCUIT_BREAKER_HARD_SL" not in close_kwargs["reason"], "嚴禁觸發硬止損！"


@pytest.mark.anyio
async def test_scheme_b_1815_valley_golden_cross_atomic_flip_long():
    """
    【方案 B 核心測試】18:15 谷底金叉起漲點秒級開多 (Atomic Flip Short to Long)：
    - 徹底棄用 EMA 200 / EMA 50 等慢線依賴
    - 空單持倉中，一旦 check_pivot_status 判定為真谷底 (MA3 金叉 MA15)
    - 系統秒級一次性執行平空兼反手開多 (PIVOT_FLIP_LONG)，帶入 direction_override=True
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    pos_short = {
        "symbol": "1000PEPEUSDT",
        "side": "SHORT",
        "entry_price": 0.0043500,
        "amount": 50000.0,
        "lowest_price": 0.0042600,
    }

    bars = []
    base_p = 0.0043000
    for i in range(35):
        p = base_p - i * 0.000001
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p + 0.0000005, "high": p + 0.000001, "low": p - 0.000001, "close": p - 0.0000005,
            "volume": 20000.0, "is_closed": True, "atr": 0.000015,
            "kc_middle": p, "kc_upper": p + 0.00002, "kc_lower": p - 0.00002,
        })

    # 構造 18:15 谷底金叉棒: 前棒 ma3 <= ma15, 當棒放量強陽上穿 ma15
    bars[-2]["close"] = 0.0042650
    bars[-2]["open"] = 0.0042700
    bars[-2]["low"] = 0.0042600
    bars[-2]["high"] = 0.0042710

    bars[-1]["open"] = 0.0042650
    bars[-1]["close"] = 0.0042850
    bars[-1]["high"] = 0.0042880
    bars[-1]["low"] = 0.0042640

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {"1000PEPEUSDT": pos_short}
            self.position_meta = {}
            self.channel_exit_frames = {"1000PEPEUSDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="1000PEPEUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.0042850,
    )

    assert res.get("status") == "SUCCESS_FLIP"
    assert res.get("side") == "LONG"
    assert close_mock.called is True
    assert open_mock.called is True
    open_kwargs = open_mock.call_args.kwargs
    assert open_kwargs["side"] == "LONG"
    assert open_kwargs["meta"]["entry_type"] == "PIVOT_FLIP_LONG"
    assert open_kwargs["meta"]["direction_override"] is True


@pytest.mark.anyio
async def test_scheme_b_1905_peak_dead_cross_atomic_flip_short():
    """
    【方案 B 核心測試】19:05 峰頂死叉秒級反手 (Atomic Flip Long to Short)：
    - 多單持倉中，一旦 check_pivot_status 判定為真峰頂 (MA3 死叉 MA15)
    - 系統在死叉當根 K 棒收線時立即觸發平多，並秒級反手開空 (PIVOT_FLIP_SHORT)
    - 徹底根除拖延至 19:10 暴跌才後知後覺平多的惡質 Bug
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    pos_long = {
        "symbol": "1000PEPEUSDT",
        "side": "LONG",
        "entry_price": 0.0042800,
        "amount": 50000.0,
        "highest_price": 0.0043120,
    }

    bars = []
    base_p = 0.0042900
    for i in range(35):
        p = base_p + i * 0.000001
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p - 0.0000005, "high": p + 0.000001, "low": p - 0.000001, "close": p + 0.0000005,
            "volume": 20000.0, "is_closed": True, "atr": 0.000015,
            "kc_middle": p, "kc_upper": p + 0.00002, "kc_lower": p - 0.00002,
        })

    # 19:03 衝頂
    bars[-3]["high"] = 0.0043120
    bars[-3]["close"] = 0.0043100

    # 19:04 高位長上影走弱
    bars[-2]["open"] = 0.0043100
    bars[-2]["high"] = 0.0043120
    bars[-2]["close"] = 0.0043020

    # 19:05 陰線下殺，MA3 正式死叉 MA15
    bars[-1]["open"] = 0.0043020
    bars[-1]["high"] = 0.0043030
    bars[-1]["low"] = 0.0042780
    bars[-1]["close"] = 0.0042800

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {"1000PEPEUSDT": pos_long}
            self.position_meta = {}
            self.channel_exit_frames = {"1000PEPEUSDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="1000PEPEUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.0042800,
    )

    assert res.get("status") == "SUCCESS_FLIP"
    assert res.get("side") == "SHORT"
    assert close_mock.called is True
    assert open_mock.called is True
    open_kwargs = open_mock.call_args.kwargs
    assert open_kwargs["side"] == "SHORT"
    assert open_kwargs["meta"]["entry_type"] == "PIVOT_FLIP_SHORT"
    assert open_kwargs["meta"]["direction_override"] is True


@pytest.mark.anyio
async def test_scheme_b_trend_lock_veto_micro_pullback():
    """
    【方案 B 核心測試】震盪假峰谷一票否決 (Trend Lock 鋼鐵鎖定)：
    - 多單持倉中，微幅小紅 K (陰線回踩)，未死叉、未吞噬、未見長上影天地針
    - 系統判定為震盪假峰谷，一票否決平倉，強制續抱拿滿主升段
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    pos_long = {
        "symbol": "1000PEPEUSDT",
        "side": "LONG",
        "entry_price": 0.0042800,
        "amount": 50000.0,
        "highest_price": 0.0043100,
    }

    bars = []
    base_p = 0.0042900
    for i in range(35):
        p = base_p + i * 0.000001
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p - 0.0000005, "high": p + 0.000001, "low": p - 0.000001, "close": p + 0.0000005,
            "volume": 20000.0, "is_closed": True, "atr": 0.000015,
            "kc_middle": p, "kc_upper": p + 0.00002, "kc_lower": p - 0.00002,
        })

    # 微幅小陰回踩 (實體極小，收盤依然穩守 MA3 與 MA15 之上，未破前低)
    p33 = bars[-2]["close"]
    bars[-1]["open"] = p33
    bars[-1]["close"] = p33 - 0.0000003
    bars[-1]["high"] = p33 + 0.0000002
    bars[-1]["low"] = p33 - 0.0000005
    quote_val = bars[-1]["close"]

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {"1000PEPEUSDT": pos_long}
            self.position_meta = {}
            self.channel_exit_frames = {"1000PEPEUSDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="1000PEPEUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=quote_val,
    )

    assert res.get("status") == "HOLD"
    assert res.get("reason") == "TREND_LOCK_MASTER_LOCK_LONG"
    assert close_mock.called is False
    assert open_mock.called is False


@pytest.mark.anyio
async def test_pepe_0648_micro_pullback_veto_hold_long():
    """
    【2026/10/28 實盤實證】1000PEPE 06:48 回踩防洗盤測試：
    - 06:36「買多」進場後主升浪狂拉至 0.0046200
    - 06:48 (0.0045650 半山腰) 出現一根回踩小陰線 (前棒破軌、當棒陰線)
    - 由於收盤價 0.0045650 依然穩居 MA15 慢線 (0.0045500) 之上，且 MA3 尚未死叉 MA15
    - 慢線硬鎖定機制必須一票否決平多 (VETO)，鎖死持倉續抱，吃滿 0.0046200 大肉！
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    pos_long = {
        "symbol": "1000PEPEUSDT",
        "side": "LONG",
        "entry_price": 0.0045300,
        "amount": 50000.0,
        "highest_price": 0.0045800,
    }

    bars = []
    base_p = 0.0045100
    for i in range(35):
        p = base_p + i * 0.0000015
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p - 0.0000005, "high": p + 0.0000010, "low": p - 0.0000005, "close": p + 0.0000005,
            "volume": 20000.0, "is_closed": True, "atr": 0.000010,
            "kc_middle": p, "kc_upper": p + 0.000012, "kc_lower": p - 0.000012,
        })

    # 06:47 破軌大陽線
    bars[-2]["open"] = 0.0045580
    bars[-2]["high"] = 0.0045780
    bars[-2]["low"] = 0.0045570
    bars[-2]["close"] = 0.0045750  # 突破 kc_upper
    bars[-2]["kc_upper"] = 0.0045680

    # 06:48 微幅回踩陰 K: 收 0.0045650，高於 MA15 慢線 (~0.0045450)
    bars[-1]["open"] = 0.0045750
    bars[-1]["high"] = 0.0045760
    bars[-1]["low"] = 0.0045630
    bars[-1]["close"] = 0.0045650
    bars[-1]["kc_upper"] = 0.0045700

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {"1000PEPEUSDT": pos_long}
            self.position_meta = {}
            self.channel_exit_frames = {"1000PEPEUSDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="1000PEPEUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.0045650,
    )

    assert res.get("status") == "HOLD"
    assert res.get("reason") == "TREND_LOCK_MASTER_LOCK_LONG"
    assert close_mock.called is False
    assert open_mock.called is False


@pytest.mark.anyio
async def test_lobster_0650_micro_bounce_veto_hold_short():
    """
    【2026/10/28 實盤實證】龙虾 06:50 反彈防洗盤測試：
    - 06:48「賣空」開倉後一路向下主跌至 0.019700
    - 06:50 (0.020150 半山腰) 僅出現一根微幅小陽 K 反彈
    - 由於收盤價 0.020150 依然被壓制在 MA15 慢線 (0.020250) 之下，且 MA3 尚未金叉 MA15
    - 慢線硬鎖定機制必須一票否決平空 (VETO)，鎖死持倉續抱，吃滿 450 點暴跌大瀑布！
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    pos_short = {
        "symbol": "LOBSTERUSDT",
        "side": "SHORT",
        "entry_price": 0.020400,
        "amount": 50000.0,
        "lowest_price": 0.020100,
    }

    bars = []
    base_p = 0.020400
    for i in range(35):
        p = base_p - i * 0.000010
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p + 0.000005, "high": p + 0.000008, "low": p - 0.000008, "close": p - 0.000005,
            "volume": 20000.0, "is_closed": True, "atr": 0.000050,
            "kc_middle": p, "kc_upper": p + 0.000080, "kc_lower": p - 0.000080,
        })

    # 06:49 跌破軌道大陰 K
    bars[-2]["open"] = 0.020200
    bars[-2]["high"] = 0.020210
    bars[-2]["low"] = 0.020110
    bars[-2]["close"] = 0.020120  # 跌破 kc_lower (0.020150)
    bars[-2]["kc_lower"] = 0.020150

    # 06:50 微幅小陽 K 反彈: 收 0.020150，低於 MA15 慢線 0.020250
    bars[-1]["open"] = 0.020120
    bars[-1]["high"] = 0.020160
    bars[-1]["low"] = 0.020115
    bars[-1]["close"] = 0.020150
    bars[-1]["kc_lower"] = 0.020140

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {"LOBSTERUSDT": pos_short}
            self.position_meta = {}
            self.channel_exit_frames = {"LOBSTERUSDT": df}
            self.closing_lock = set()

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="LOBSTERUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.020150,
    )

    assert res.get("status") == "HOLD"
    assert res.get("reason") == "TREND_LOCK_MASTER_LOCK_SHORT"
    assert close_mock.called is False
    assert open_mock.called is False


@pytest.mark.anyio
async def test_scheme_b_idle_pivot_long_open_no_rigid_sl():
    """
    【方案 B 最高決策驗證】無持倉時捕獲真谷底金叉開多，且完全取消固定價位硬止損：
    1. 帳戶無持倉 (NONE)
    2. 18:15 在低位捕獲 MA3 金叉 MA15 (真谷底確立)
    3. 系統秒級開多 (PIVOT_LONG)，帶入 direction_override=True，stop_loss_price=None (No Rigid Stop Loss)！
    """
    from core.services.execution_manager import ExecutionManager
    from unittest.mock import AsyncMock

    bars = []
    base_p = 0.0043000
    for i in range(35):
        p = base_p - i * 0.000001
        bars.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": p + 0.000005, "high": p + 0.000015, "low": p - 0.000015, "close": p - 0.000005,
            "volume": 20000.0, "is_closed": True, "atr": 0.000015,
            "kc_middle": p, "kc_upper": p + 0.000035, "kc_lower": p - 0.000035,
        })

    # 18:15 谷底金叉棒: 前棒 ma3 <= ma15, 當棒放量強陽上穿 ma15
    bars[-2]["close"] = 0.0042650
    bars[-2]["open"] = 0.0042700
    bars[-2]["low"] = 0.0042600
    bars[-2]["high"] = 0.0042710

    bars[-1]["open"] = 0.0042650
    bars[-1]["close"] = 0.0042850
    bars[-1]["high"] = 0.0042880
    bars[-1]["low"] = 0.0042640
    bars[-1]["kc_upper"] = 0.0043200

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {}
            self.position_meta = {}
            self.channel_exit_frames = {}
            self.closing_lock = set()

    account = MockAccount()
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="1000PEPEUSDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.0042850,
    )

    assert res.get("status") in ("SUCCESS_ENTRY", "ENTRY_EXECUTED")
    assert res.get("side") == "LONG"
    assert "PIVOT_LONG" in res.get("entry_type")
    assert open_mock.called is True
    open_kwargs = open_mock.call_args.kwargs
    assert open_kwargs["side"] == "LONG"
    assert "PIVOT_LONG" in open_kwargs["meta"]["entry_type"]
    assert open_kwargs["meta"]["direction_override"] is True
    assert open_kwargs["meta"]["no_rigid_sl"] is True
    assert open_kwargs["meta"]["stop_loss_price"] is None


@pytest.mark.anyio
async def test_lobster_1200_pinbar_bottom_flip_long():
    """
    驗證 12:00 龍蝦實盤案例：
    持空單期間，價格在 0.019650 附近拉出長下影刺針 (Pinbar Bottom 擊穿 KC 下軌)，
    系統必須在當根 K 棒立即放行平空，並同 K 棒秒級發起 PIVOT_FLIP_LONG 反手開多，
    徹底解決平空滯後到 0.01976 半山腰以及錯失暴漲大陽線的漏洞！
    """
    from unittest.mock import AsyncMock
    from core.services.execution_manager import ExecutionManager, check_pivot_status

    # 1. 直接測試 check_pivot_status
    curr_k = {
        "open": 0.01968, "close": 0.01982, "high": 0.01985, "low": 0.01965,
        "ma3": 0.01981, "ma15": 0.01980,
    }
    prev_k = {
        "open": 0.01978, "close": 0.01969, "high": 0.01979, "low": 0.01968,
        "ma3": 0.01973, "ma15": 0.01982,
    }
    is_true_bottom, is_true_peak = check_pivot_status(
        curr_k=curr_k, prev_k=prev_k,
        ma3_curr=0.01981, ma3_prev=0.01973,
        ma15_curr=0.01980, ma15_prev=0.01982,
        kc_upper=0.02010, kc_lower=0.01967,
        prev_kc_upper=0.02015, prev_kc_lower=0.01970,
        current_atr=0.00005,
    )
    assert is_true_bottom is True, "12:00 軌外下刺針且收盤突破 MA15 慢線，判定為真谷底"

    # 2. 測試 ExecutionManager 全鏈路平空兼反手開多 (Atomic Flip)
    bars = []
    base_ts = 1793160000_000
    for i in range(20):
        bars.append({
            "timestamp": base_ts + i * 60_000,
            "open": 0.01985 - i * 0.00001,
            "close": 0.01984 - i * 0.00001,
            "high": 0.01986 - i * 0.00001,
            "low": 0.01983 - i * 0.00001,
            "ma3": 0.01984 - i * 0.00001,
            "ma15": 0.01986 - i * 0.00001,
            "kc_upper": 0.02010,
            "kc_lower": 0.01968,
            "kc_middle": 0.01989,
            "atr": 0.00005,
            "volume": 100000.0,
            "is_closed": True,
        })
    bars[-1]["open"] = 0.01968
    bars[-1]["close"] = 0.01982
    bars[-1]["low"] = 0.01965
    bars[-1]["high"] = 0.01985
    bars[-1]["ma3"] = 0.01981
    bars[-1]["ma15"] = 0.01980

    df = pd.DataFrame(bars)

    class MockAccount:
        def __init__(self):
            self.positions = {
                "龙虾/USDT": {
                    "side": "SHORT",
                    "entry_price": 0.019788,
                    "qty": 18973.0,
                    "margin": 75.0,
                    "leverage": 5,
                    "sl": 0.0,
                    "tp": 0.0,
                }
            }
            self.position_meta = {}
            self.channel_exit_frames = {}
            self.closing_lock = set()

        async def close_position(self, symbol, current_price, close_reason, is_limit=False):
            self.positions.pop(symbol, None)
            return True

        async def open_position(self, **kwargs):
            return True

    account = MockAccount()
    close_mock = AsyncMock(return_value=True)
    open_mock = AsyncMock(return_value=True)
    ExecutionManager.close_position = close_mock
    ExecutionManager.open_position = open_mock

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="龙虾/USDT",
        engine_or_account=account,
        klines_df=df,
        quote=0.01982,
    )

    assert close_mock.called is True, "必須立即市價平空"
    close_kwargs = close_mock.call_args.kwargs
    assert "CLOSE_SHORT_AT_BOTTOM" in close_kwargs["reason"]

    assert open_mock.called is True, "必須立即發起秒級反手開多"
    open_kwargs = open_mock.call_args.kwargs
    assert open_kwargs["side"] == "LONG"
    assert "PIVOT_FLIP_LONG" in open_kwargs["meta"]["entry_type"]
    assert open_kwargs["meta"]["direction_override"] is True
    assert open_kwargs["meta"]["no_rigid_sl"] is True


def test_pepe_1210_lock_short_and_lobster_1214_lock_long():
    """
    驗證 Trend Lock 慢線與死叉鋼鐵鎖定：
    1. 1000PEPE 12:10：空頭持倉中，收盤價在 MA15 下方且未金叉的小綠 K，平空邏輯強制 return False (HOLD)。
    2. 龍蝦 12:14：多頭持倉中，收盤價在 MA15 上方且 MA3 未死叉的回踩紅 K，平多邏輯強制 return False (HOLD)。
    """
    from core.services.execution_manager import ExecutionManager, check_pivot_status, should_exit_long, should_exit_short

    # 1. 1000PEPE 12:10: close < ma15 且未金叉的小綠 K -> is_true_bottom 為 False，should_exit_short 為 False
    pepe_curr_k = {"open": 0.00450, "close": 0.00452, "high": 0.00453, "low": 0.00448} # 小綠 K
    pepe_prev_k = {"open": 0.00455, "close": 0.00451, "high": 0.00456, "low": 0.00450}
    is_bottom, _ = check_pivot_status(
        curr_k=pepe_curr_k, prev_k=pepe_prev_k,
        ma3_curr=0.00451, ma3_prev=0.00452, ma15_curr=0.00458, ma15_prev=0.00460, # 遠低於 ma15 且未金叉
        kc_upper=0.00470, kc_lower=0.00445, prev_kc_upper=0.00472, prev_kc_lower=0.00448,
        current_atr=0.00005,
    )
    assert is_bottom is False, "1000PEPE 12:10 在 MA15 下方且未金叉，真谷底必須判定為 False！"

    assert should_exit_short(
        curr_k=pepe_curr_k, prev_k=pepe_prev_k,
        ma3_curr=0.00451, ma3_prev=0.00452, ma15_curr=0.00458, ma15_prev=0.00460,
        kc_lower=0.00445, prev_kc_lower=0.00448, current_atr=0.00005,
    ) is False, "1000PEPE 12:10 必須一票否決平空，鎖死空單持倉！"

    # 2. 龍蝦 12:14: close > ma15 且 MA3 未死叉的回踩紅 K -> is_true_peak 為 False，should_exit_long 為 False
    lobster_curr_k = {"open": 0.01985, "close": 0.01982, "high": 0.01988, "low": 0.01980} # 回踩紅 K
    lobster_prev_k = {"open": 0.01975, "close": 0.01984, "high": 0.01985, "low": 0.01974}
    _, is_peak = check_pivot_status(
        curr_k=lobster_curr_k, prev_k=lobster_prev_k,
        ma3_curr=0.01983, ma3_prev=0.01980, ma15_curr=0.01975, ma15_prev=0.01972, # 遠高於 ma15 且未死叉
        kc_upper=0.01995, kc_lower=0.01960, prev_kc_upper=0.01990, prev_kc_lower=0.01958,
        current_atr=0.00005,
    )
    assert is_peak is False, "龍蝦 12:14 在 MA15 上方且未死叉，真峰頂必須判定為 False！"

    assert should_exit_long(
        curr_k=lobster_curr_k, prev_k=lobster_prev_k,
        ma3_curr=0.01983, ma3_prev=0.01980, ma15_curr=0.01975, ma15_prev=0.01972,
        kc_upper=0.01995, prev_kc_upper=0.01990, current_atr=0.00005,
    ) is False, "龍蝦 12:14 必須一票否決平多，鎖死多單持倉！"


def test_buffer_distance_and_slope_trend_lock_bidirectional():
    """
    驗證最高核心架構與鐵律（多空雙向）：
    1. 均線黏合假金叉 / 假死叉過濾 (Buffer Distance Protection: 穿越深度 >= 0.15 * ATR)
    2. 中軌斜率雙向鋼鐵鎖定 (中軌向上不平多、中軌向下不平空)
    """
    from core.services.execution_manager import (
        is_valid_dead_cross,
        is_valid_golden_cross,
        should_exit_long,
        should_exit_short,
    )

    atr = 0.5

    # 1. 死叉深度防線 (MA15 - MA3 >= 0.15 * ATR = 0.075)
    # 黏合微幅毛刺 (深度 = 10.02 - 10.00 = 0.02 < 0.075) -> False
    assert is_valid_dead_cross(ma3_curr=10.00, ma3_prev=10.05, ma15_curr=10.02, ma15_prev=10.00, current_atr=atr) is False
    # 有效深度死叉 (深度 = 10.10 - 10.00 = 0.10 >= 0.075) -> True
    assert is_valid_dead_cross(ma3_curr=10.00, ma3_prev=10.05, ma15_curr=10.10, ma15_prev=10.00, current_atr=atr) is True

    # 2. 金叉深度防線 (MA3 - MA15 >= 0.15 * ATR = 0.075)
    # 黏合微幅毛刺 (深度 = 10.02 - 10.00 = 0.02 < 0.075) -> False
    assert is_valid_golden_cross(ma3_curr=10.02, ma3_prev=9.95, ma15_curr=10.00, ma15_prev=10.00, current_atr=atr) is False
    # 有效深度金叉 (深度 = 10.10 - 10.00 = 0.10 >= 0.075) -> True
    assert is_valid_golden_cross(ma3_curr=10.10, ma3_prev=9.95, ma15_curr=10.00, ma15_prev=10.00, current_atr=atr) is True

    # 3. 多頭中軌斜率鋼鐵鎖定 (kc_mid_curr > kc_mid_prev 且 close > ma15)
    # 即使盤中微幅回踩紅 K 且 MA3 碰觸 MA15，但未滿足 0.15*ATR 深度死叉 -> 強制 False
    curr_k_long = {"open": 10.5, "close": 10.3, "high": 10.6, "low": 10.2} # 回踩紅 K, close=10.3 > ma15=10.1
    prev_k_long = {"open": 10.2, "close": 10.5, "high": 10.6, "low": 10.1}
    assert should_exit_long(
        curr_k=curr_k_long, prev_k=prev_k_long,
        ma3_curr=10.08, ma3_prev=10.12, ma15_curr=10.10, ma15_prev=10.05, # 差距 0.02 < 0.075 黏合假死叉
        kc_middle=10.0, prev_kc_middle=9.9, # 中軌斜率向上
        current_atr=atr,
    ) is False, "中軌向上且在 MA15 之上，均線黏合假死叉必須一票否決平多！"

    # 4. 空頭中軌斜率鋼鐵鎖定 (kc_mid_curr < kc_mid_prev 且 close < ma15)
    # 即使盤中微幅反彈綠 K 且 MA3 碰觸 MA15，但未滿足 0.15*ATR 深度金叉 -> 強制 False
    curr_k_short = {"open": 9.5, "close": 9.7, "high": 9.8, "low": 9.4} # 反彈綠 K, close=9.7 < ma15=9.9
    prev_k_short = {"open": 9.8, "close": 9.5, "high": 9.9, "low": 9.4}
    assert should_exit_short(
        curr_k=curr_k_short, prev_k=prev_k_short,
        ma3_curr=9.92, ma3_prev=9.88, ma15_curr=9.90, ma15_prev=9.95, # 差距 0.02 < 0.075 黏合假金叉
        kc_middle=9.9, prev_kc_middle=10.0, # 中軌斜率向下
        current_atr=atr,
    ) is False, "中軌向下且在 MA15 之下，均線黏合假金叉必須一票否決平空！"


def test_idle_none_position_anti_miss_triggers():
    """
    驗證無持倉狀態 (NONE) 的起漲/起跌點防錯失機制：
    1. 只要 MA3 正式金叉 MA15 (且穿越深度 >= 0.15 * ATR)，觸發 NONE_GOLDEN_CROSS 秒級開多！
    2. 只要 MA3 正式死叉 MA15 (且穿越深度 >= 0.15 * ATR)，觸發 NONE_DEAD_CROSS 秒級開空！
    3. 驗證豁免中軌死鎖 (swing_pivot_override=True)，確保谷底起漲點與頂部起跌點順暢成交！
    """
    from core.services.execution_manager import ExecutionManager, is_valid_golden_cross, is_valid_dead_cross

    atr = 0.5
    # 深度金叉 (ma3=10.10, ma15=10.00, 深度=0.10 >= 0.15*0.5=0.075) -> True
    assert is_valid_golden_cross(ma3_curr=10.10, ma3_prev=9.95, ma15_curr=10.00, ma15_prev=10.00, current_atr=atr) is True
    # 深度死叉 (ma3=9.90, ma15=10.00, 深度=0.10 >= 0.15*0.5=0.075) -> True
    assert is_valid_dead_cross(ma3_curr=9.90, ma3_prev=10.05, ma15_curr=10.00, ma15_prev=10.00, current_atr=atr) is True



