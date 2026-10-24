# -*- coding: utf-8 -*-
"""
測試：破位起跌進場 (Breakdown Trigger) 與負乖離硬鎖 (Overextended Veto)，以及空單 MA3 鎖倉與雙根轉折驗證
對應 19:18 起跌波段、19:31 地板空、19:34 十字星防洗盤與雙根轉折實盤場景
"""

import pandas as pd
import pytest
from core.gates.unified_pipeline_gates import EntryGate, ExitGate


def test_breakdown_trigger_allows_early_short():
    """
    場景一（19:18 破位起跌即進場）：
    Completed Bar 大陰線收破 KC 中軌，MA3 向下拐頭，
    此時 KC 中軌與 MA15 剛起跌尚未大角度下傾 (走平，斜率約 0)。
    驗證：EntryGate 允許開空，不被 VETO_KC_MIDDLE_FLAT 或 VETO_SELL_KC_NOT_DOWN 誤殺！
    """
    atr = 10.0
    kc_mid = 1000.0
    kc_up = 1020.0
    kc_low = 980.0

    # 模擬 19:18 大陰線收破中軌：Open 1002, Close 996 (跌破中軌，body=6 >= 0.35*ATR)
    # MA3 向下拐頭: curr=997 < prev=1001
    # 中長均線剛走平: slope=0
    df = pd.DataFrame({
        "open": [1003.0, 1002.0, 1002.0, 1002.0],
        "high": [1005.0, 1004.0, 1003.0, 1003.0],
        "low": [1000.0, 999.0, 998.0, 995.0],
        "close": [1002.0, 1001.0, 1000.0, 996.0],
        "kc_middle": [kc_mid, kc_mid, kc_mid, kc_mid],
        "kc_basis": [kc_mid, kc_mid, kc_mid, kc_mid],
        "kc_upper": [kc_up, kc_up, kc_up, kc_up],
        "kc_lower": [kc_low, kc_low, kc_low, kc_low],
        "ma3": [1002.0, 1001.5, 1001.0, 997.33],
        "ma5": [1002.0, 1001.5, 1001.0, 998.0],
        "ma15": [kc_mid, kc_mid, kc_mid, kc_mid],
        "atr": [atr, atr, atr, atr],
        "is_closed": [True, True, True, True],
    })

    curr_k = {
        "open": 1002.0,
        "high": 1003.0,
        "low": 995.0,
        "close": 996.0,
        "volume": 100.0,
        "kc_middle": kc_mid,
        "kc_upper": kc_up,
        "kc_lower": kc_low,
        "ma3": 997.33,
        "ma5": 998.0,
        "ma15": kc_mid,
        "atr": atr,
    }
    prev_k = {
        "open": 1002.0,
        "high": 1003.0,
        "low": 998.0,
        "close": 1000.0,
        "kc_middle": kc_mid,
        "kc_upper": kc_up,
        "kc_lower": kc_low,
        "ma3": 1001.0,
        "ma5": 1001.0,
        "ma15": kc_mid,
        "atr": atr,
    }

    market_data = {
        "current_price": 995.5,
        "atr": atr,
        "kc_middle": kc_mid,
        "kc_upper": kc_up,
        "kc_lower": kc_low,
        "kc_middle_slope": 0.0,
        "macro_trend": "NEUTRAL",
        "min_bandwidth_threshold": 0.005,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": df,
        "ema_fast": kc_mid,
        "ema_slow": kc_mid,
    }

    ok, reason, meta = EntryGate.evaluate(
        signal_type="MA_CROSS_SHORT",
        market_data=market_data,
        atr=atr,
        kc_slope=0.0,
    )

    assert ok is True, f"應成功允許破位起跌開空，但被拒絕: {reason}, meta={meta}"


def test_overextended_veto_blocks_floor_short():
    """
    場景二（19:31 負乖離硬鎖拒絕地板空）：
    行情在 19:25～19:29 橫盤減速後，往下破低，
    價格距中軌負乖離過大（kc_mid - close > 1.5 * ATR）或已緊貼 KC 下軌 (<= 0.2 ATR)，
    驗證：EntryGate 100% 一票否決 VETO_OVEREXTENDED_SHORT！
    """
    atr = 10.0
    kc_mid = 1000.0
    kc_up = 1020.0
    kc_low = 980.0

    # 價格跌至 981 (距中軌 gap = 19 > 1.5*ATR=15，且距下軌 1.0 <= 0.2*ATR=2.0)
    curr_k = {
        "open": 983.0,
        "high": 984.0,
        "low": 980.5,
        "close": 981.0,
        "kc_middle": kc_mid,
        "kc_upper": kc_up,
        "kc_lower": kc_low,
        "ma3": 982.0,
        "ma5": 983.0,
        "ma15": 995.0,
        "atr": atr,
    }
    prev_k = {
        "open": 985.0,
        "high": 986.0,
        "low": 982.0,
        "close": 983.0,
        "kc_middle": kc_mid,
        "kc_upper": kc_up,
        "kc_lower": kc_low,
        "ma3": 984.0,
        "ma5": 985.0,
        "ma15": 996.0,
        "atr": atr,
    }

    df = pd.DataFrame([prev_k, curr_k])
    df["is_closed"] = True

    market_data = {
        "current_price": 981.0,
        "atr": atr,
        "kc_middle": kc_mid,
        "kc_upper": kc_up,
        "kc_lower": kc_low,
        "kc_middle_slope": -0.15,
        "macro_trend": "BEARISH",
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": df,
        "ema_fast": 990.0,
        "ema_slow": kc_mid,
    }

    ok, reason, meta = EntryGate.evaluate(
        signal_type="BREAKDOWN_CONTINUATION",
        market_data=market_data,
        atr=atr,
        kc_slope=-0.15,
    )

    assert ok is False, "超賣地板空必須被阻斷！"
    assert "VETO_OVEREXTENDED_SHORT" in str(reason), f"期望 VETO_OVEREXTENDED_SHORT，實際為: {reason}"


def test_holding_protection_veto_hold_on_small_green_candle():
    """
    場景三（19:34 空單頂層強制鎖倉續抱）：
    空單持倉中，收出一根微小綠十字星 (19:34)，
    但收盤價依然壓在紅色 MA3 之下 (Close < MA3 且 Close < KC_Middle)。
    驗證：ExitGate.evaluate 100% 觸發 VETO_HOLD_SHORT，一票否決提前平空！
    """
    atr = 10.0
    kc_mid = 1000.0

    # 持有空單，入場價 995
    pos = {
        "side": "SHORT",
        "entry_price": 995.0,
        "current_price": 982.0,
        "highest_price": 995.0,
        "lowest_price": 980.0,
        "unrealized_profit": 13.0,
        "unrealized_profit_pct": 0.013,
    }

    # 19:34 單根小陽棒/綠十字星: Open 981, Close 982, High 983, Low 980.5
    # MA3 在 984 (Close 982 依然被 MA3 與 KC 中軌牢牢壓制)
    df = pd.DataFrame([
        {"open": 985.0, "high": 986.0, "low": 982.0, "close": 983.0, "kc_middle": kc_mid, "ma3": 986.0, "ma5": 987.0, "atr": atr, "is_closed": True},
        {"open": 983.0, "high": 984.0, "low": 980.0, "close": 981.0, "kc_middle": kc_mid, "ma3": 984.0, "ma5": 985.0, "atr": atr, "is_closed": True},
        {"open": 981.0, "high": 983.0, "low": 980.5, "close": 982.0, "kc_middle": kc_mid, "ma3": 983.5, "ma5": 984.0, "atr": atr, "is_closed": True},
    ])

    market_data = {
        "current_price": 982.0,
        "atr": atr,
        "kc_middle": kc_mid,
        "kc_middle_slope": -0.1,
        "ma3": 983.5,
        "ma15": 995.0,
        "curr_k": df.iloc[-1].to_dict(),
        "prev_k": df.iloc[-2].to_dict(),
        "raw_df": df,
    }

    should_exit, reason, meta = ExitGate.evaluate(
        pos=pos,
        market_data=market_data,
        current_price=982.0,
        atr=atr,
        kc_slope=-0.1,
    )

    assert should_exit is False, f"空單壓制在 MA3 之下嚴禁平空！但觸發了平倉: {reason}"
    assert "VETO_HOLD" in str(reason), f"期望 VETO_HOLD，實際為: {reason}"


def test_valley_exit_requires_two_bullish_candles_and_ma3_reclaim():
    """
    場景四（頂底確認必須雙根驗證才平空）：
    必須出現「連續 2 根 Completed Bar 收陽 + 實體突破站穩 MA3 之上」，
    才算真正轉折，此時才允許獲利平倉離場。
    """
    atr = 10.0
    kc_mid = 1000.0

    pos = {
        "side": "SHORT",
        "entry_price": 1010.0,
        "current_price": 988.0,
        "highest_price": 1010.0,
        "lowest_price": 980.0,
        "unrealized_profit": 22.0,
        "unrealized_profit_pct": 0.022,
        "leverage": 5.0,
    }

    # 構造連續 2 根陽棒並突破站穩 MA3 (ma5_curr = 985, close2 = 988 > 985)
    # 前一棒 (谷底最低): low 980, close 982
    # 棒1 (收陽): open 981, close 984
    # 棒2 (收陽且站穩 MA3): open 984, close 988 (站上 MA3 985)
    df = pd.DataFrame([
        {"open": 985.0, "high": 986.0, "low": 980.0, "close": 982.0, "kc_middle": kc_mid, "kc_upper": 1020.0, "kc_lower": 980.0, "ma3": 985.0, "ma5": 985.0, "ma15": 995.0, "atr": atr, "is_closed": True},
        {"open": 981.0, "high": 985.0, "low": 981.0, "close": 984.0, "kc_middle": kc_mid, "kc_upper": 1020.0, "kc_lower": 980.0, "ma3": 984.0, "ma5": 984.0, "ma15": 995.0, "atr": atr, "is_closed": True},
        {"open": 984.0, "high": 989.0, "low": 983.5, "close": 988.0, "kc_middle": kc_mid, "kc_upper": 1020.0, "kc_lower": 980.0, "ma3": 985.0, "ma5": 985.0, "ma15": 995.0, "atr": atr, "is_closed": True},
    ])

    from core.gates.top_bottom_reversal_exit import evaluate_top_bottom_reversal_exit
    tb_exit, tb_reason, tb_meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=atr)

    assert tb_exit is True, f"雙根陽線且站穩 MA3 應觸發真谷底平空，但返回: {tb_reason}"
    assert "VALLEY" in str(tb_reason) or "BOTTOM" in str(tb_reason)
