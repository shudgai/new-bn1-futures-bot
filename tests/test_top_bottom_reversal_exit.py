"""
tests/test_top_bottom_reversal_exit.py
======================================
驗證極值轉折點（V 轉 / 倒 V 轉）「見頂/見底即平倉」規則（2026-10-23 最新授權）：
1. 空單：
   - MA3 谷底勾頭 (V 轉底)
   - 破下軌長下影線探底回升 (Pin Bar)
   - 低檔陽包陰吞噬反轉 (Bullish Engulfing)
   - 盤面真實痛點情境：黑圈位置出現長下影線+MA3勾頭，不等站回中軌立即平空獲利了結！
2. 多單：
   - MA3 峰頂下彎 (倒 V 轉頂)
   - 破上軌長上影線衝高回落 (Pin Bar)
   - 高檔陰包陽吞噬反轉 (Bearish Engulfing)
3. 防抖動保護：
   - 開倉同根棒保護 (ENTRY_BAR_PROTECTION)
   - 無波段展開防抖 (NO_FAVORABLE_MOVE_YET)
4. ExitGate 整合：
   - 一票否決所有普通 VETO_HOLD，直接平倉鎖利！
   - 安全硬止損優先於頂底出場。
"""
import pandas as pd
import pytest

from core.gates.top_bottom_reversal_exit import (
    CLOSE_SHORT_AT_BOTTOM,
    CLOSE_LONG_AT_TOP,
    evaluate_top_bottom_reversal_exit,
)
from core.gates.unified_pipeline_gates import ExitGate

MID = 100.0
UP = 105.0
LOW = 95.0
ATR = 2.0


def make_bar(o, h, l, c, mid=MID, up=UP, low=LOW, atr=ATR, is_closed=True, ts=None):
    b = {
        "open": float(o), "high": float(h), "low": float(l), "close": float(c),
        "kc_middle": float(mid), "kc_upper": float(up), "kc_lower": float(low),
        "atr": float(atr), "is_closed": is_closed,
    }
    if ts is not None:
        b["timestamp"] = ts
    return b


LIVE = make_bar(94.0, 94.5, 93.8, 94.2, is_closed=False)


def make_df(*bars):
    return pd.DataFrame(list(bars) + [LIVE])


# ══════════════════════════════════════════════════════
# 1. 空單測試 (SHORT)
# ══════════════════════════════════════════════════════

def test_short_two_bars_bullish_and_ma3_breakout_exits():
    """空單下殺破軌後，出現連續 2 根 Completed Bar 收陽且突破 MA3，確立真谷底平空"""
    bars = [
        make_bar(97.0, 97.2, 95.8, 96.0),
        make_bar(96.0, 96.2, 92.8, 93.0),
        make_bar(93.0, 93.2, 90.8, 91.0),   # 探破下軌 95.0，Stage A 達成
        make_bar(91.0, 92.5, 90.9, 92.2),   # 第一根 Completed Bar 收陽
        make_bar(92.2, 94.2, 92.0, 94.0),   # 第二根 Completed Bar 收陽且實體突破 MA3 (ma3~92.4)
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 90.8}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is True
    assert reason == CLOSE_SHORT_AT_BOTTOM
    assert any("TWO_BARS_BULLISH" in t for t in meta["triggers"])
    assert any("MA3_BREAKOUT" in t for t in meta["triggers"])


def test_short_single_pin_bar_or_single_green_skipped_as_noise():
    """空單僅出現單根下影線或單根小陽棒，未達連續 2 根收陽，直接判定為雜訊禁止平倉"""
    bars = [
        make_bar(100.0, 100.2, 98.5, 99.0),
        make_bar(99.0, 99.1, 96.8, 97.0),
        make_bar(97.0, 97.2, 95.0, 95.5),   # 陰線
        # 當根：單根長下影小陽線，但前根為陰線，未達連續 2 根收陽
        make_bar(95.0, 95.6, 92.0, 95.2),
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 92.0}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False
    assert meta.get("skipped") == "STAGE_B_NOT_CONFIRMED: 未達連續2根收陽或未突破MA3或有長上影回吐，視為良性整理雜訊"


def test_short_entry_bar_protection_skips_exit():
    """剛開倉同根 K 棒時間戳保護，不被進場當根雜訊誤平"""
    entry_ts = 1_000_000
    bars = [
        make_bar(100.0, 100.2, 98.0, 98.5, ts=940_000),
        make_bar(98.5, 98.6, 96.0, 96.5, ts=960_000),
        make_bar(96.5, 96.6, 94.0, 94.5, ts=980_000),
        make_bar(94.5, 95.0, 92.0, 94.8, ts=1_000_000),  # 時間戳等於進場時間
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "open_timestamp": entry_ts}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False
    assert meta.get("skipped") == "ENTRY_BAR_PROTECTION"


def test_short_no_favorable_move_yet_skips_exit():
    """空單若完全沒有順向波段展開 (剛開在 100.0，價格完全在 100.0 以上晃)，不誤觸谷底"""
    bars = [
        make_bar(100.0, 100.5, 100.0, 100.2),
        make_bar(100.2, 100.4, 100.0, 100.1),
        make_bar(100.1, 100.3, 100.0, 100.2),
        make_bar(100.2, 100.4, 100.0, 100.1),
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 100.0}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False
    assert "STAGE_A_NOT_MET" in meta.get("skipped", "")


# ══════════════════════════════════════════════════════
# 2. 多單測試 (LONG)
# ══════════════════════════════════════════════════════

def test_long_two_bars_bearish_and_ma3_breakdown_exits():
    """多單衝高衝破上軌後，出現連續 2 根 Completed Bar 收陰且跌破 MA3，確立真峰頂平多"""
    bars = [
        make_bar(99.0, 100.2, 98.8, 100.0),
        make_bar(100.0, 103.2, 99.8, 103.0),
        make_bar(103.0, 107.5, 102.8, 107.0),  # 衝破上軌 105.0，Stage A 達成
        make_bar(107.0, 107.2, 105.5, 105.8),  # 第一根 Completed Bar 收陰
        make_bar(105.8, 106.0, 103.0, 103.5),  # 第二根 Completed Bar 收陰且實體跌破 MA3 (ma3~105.4)
    ]
    df = make_df(*bars)
    pos = {"side": "LONG", "entry_price": 100.0, "highest_price": 107.5}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is True
    assert reason == CLOSE_LONG_AT_TOP
    assert any("TWO_BARS_BEARISH" in t for t in meta["triggers"])
    assert any("MA3_BREAKDOWN" in t for t in meta["triggers"])


def test_long_single_pin_bar_or_single_red_skipped_as_noise():
    """多單衝高後僅出現單根避雷針或單根小陰線，未達連續 2 根收陰，直接判定為雜訊禁止平倉"""
    bars = [
        make_bar(100.0, 101.5, 99.8, 101.0),
        make_bar(101.0, 103.2, 100.8, 103.0),
        make_bar(103.0, 104.8, 102.8, 104.5),  # 陽線
        # 當根：單根避雷針衝高回落小陰線，但前根為陽線，未達連續 2 根收陰
        make_bar(104.5, 108.0, 104.0, 104.2),
    ]
    df = make_df(*bars)
    pos = {"side": "LONG", "entry_price": 100.0, "highest_price": 108.0}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False
    assert meta.get("skipped") == "STAGE_B_NOT_CONFIRMED: 未達連續2根收陰或未跌破MA3或有長下影回抽，視為良性回踩雜訊"


# ══════════════════════════════════════════════════════
# 3. ExitGate 整合回歸測試
# ══════════════════════════════════════════════════════

def test_exit_gate_integrates_bottom_exit_when_trend_challenged():
    """驗證 ExitGate 整合：空單下殺探底後，反彈突破 MA15 挑戰空頭結構，且完成 2 根收陽+突破 MA3，放行真谷底平倉"""
    bars = [
        make_bar(100.0, 100.2, 97.8, 98.0),
        make_bar(98.0, 98.1, 95.8, 96.0),
        make_bar(96.0, 96.2, 90.8, 91.0),   # 灌破下軌 95.0
        make_bar(91.0, 95.5, 90.9, 95.0),   # 第 1 根收陽
        make_bar(95.0, 98.2, 94.8, 98.0),   # 第 2 根收陽，突破 MA15 (97.0) 與 MA3
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 90.8, "qty": 1.0, "leverage": 5.0}
    snapshot = {
        "current_price": 98.0,
        "atr": ATR,
        "kc_upper": UP,
        "kc_middle": MID,
        "kc_lower": LOW,
        "kc_middle_slope": -0.01,
        "ma15": 97.0,
        "ema_fast": 96.0,
        "ema_slow": 98.0,
        "raw_df": df,
        "macro_trend": "BEARISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=98.0)
    assert should_exit is True
    assert reason == CLOSE_SHORT_AT_BOTTOM
    assert "top_bottom_reversal" in meta


def test_exit_gate_integrates_top_exit_when_trend_challenged():
    """驗證 ExitGate 整合：多單衝高後，回跌跌破 MA15 挑戰多頭結構，且完成 2 根收陰+跌破 MA3，放行真峰頂平倉"""
    bars = [
        make_bar(99.0, 100.5, 98.8, 100.0),
        make_bar(100.0, 102.2, 99.8, 102.0),
        make_bar(102.0, 108.5, 101.8, 108.0),  # 衝破上軌 105.0
        make_bar(108.0, 108.2, 104.5, 105.0),  # 第 1 根收陰
        make_bar(105.0, 105.2, 101.8, 102.0),  # 第 2 根收陰，跌破 MA15 (103.0) 與 MA3
    ]
    df = make_df(*bars)
    pos = {"side": "LONG", "entry_price": 100.0, "highest_price": 108.5, "qty": 1.0, "leverage": 5.0}
    snapshot = {
        "current_price": 102.0,
        "atr": ATR,
        "kc_upper": UP,
        "kc_middle": MID,
        "kc_lower": LOW,
        "kc_middle_slope": 0.01,
        "ma15": 103.0,
        "ema_fast": 104.0,
        "ema_slow": 102.0,
        "raw_df": df,
        "macro_trend": "BULLISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=102.0)
    assert should_exit is True
    assert reason == CLOSE_LONG_AT_TOP
    assert "top_bottom_reversal" in meta


def test_long_hold_when_ma3_still_rising_or_close_above_ma3():
    """驗證 21:06 痛點：MA3 仍陡峭向上或收盤價仍穩站 MA3 之上時，絕不平多，防止提早平倉！"""
    # 當根收盤 106.0，MA3 仍在向上
    bars = [
        make_bar(100.0, 101.5, 99.8, 101.0),
        make_bar(101.0, 103.5, 100.8, 103.0),
        make_bar(103.0, 105.2, 102.8, 105.0),
        make_bar(105.0, 107.0, 104.8, 106.5),  # MA3 向上
        make_bar(106.5, 107.2, 105.8, 106.8),  # 收紅陽線，MA3 繼續向上
    ]
    df = make_df(*bars)
    pos = {"side": "LONG", "entry_price": 100.0, "highest_price": 107.2}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False


def test_short_hold_when_ma3_still_falling_or_close_below_ma3():
    """驗證空單下殺時：MA3 仍陡峭向下或收盤價仍壓制在 MA3 之下時，絕不平空，防止被小反彈洗下車！"""
    bars = [
        make_bar(100.0, 100.2, 98.8, 99.0),
        make_bar(99.0, 99.2, 96.8, 97.0),
        make_bar(97.0, 97.2, 94.8, 95.0),
        make_bar(95.0, 95.2, 93.0, 93.5),
        make_bar(93.5, 93.8, 92.0, 92.5),  # 繼續破底，MA3 向下
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 92.0}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False


def test_exit_gate_holds_long_when_ma3_sloping_up_with_upper_wick():
    """
    [防線一回歸測試] 13:14 實盤痛點：
    多單開倉衝高，當根碰觸 KC 上軌並收陽線帶上影線（但非極端避雷針），
    MA3 依然強勁上揚且收盤穩踩 MA3 之上，ExitGate 必須 100% 行使 VETO_EXIT_MA3_STRONG_UP 續抱主升浪！
    """
    bars = [
        make_bar(100.0, 101.0, 99.8, 100.8),
        make_bar(100.8, 102.5, 100.5, 102.2),
        make_bar(102.2, 104.5, 102.0, 104.0),
        # 當根：開 104.0，最高衝破上軌 105.0 達 105.5，收 104.6。實體 0.6，上影線 0.9 (正常上影線，非避雷針)
        make_bar(104.0, 105.5, 103.8, 104.6),
    ]
    df = make_df(*bars)
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 105.5,
        "qty": 1.0,
        "leverage": 1.0,
    }
    snapshot = {
        "current_price": 104.6,
        "atr": ATR,
        "kc_upper": UP,
        "kc_middle": MID,
        "kc_lower": LOW,
        "kc_middle_slope": 0.02,
        "ema_fast": 103.0,
        "ema_slow": 101.0,
        "raw_df": df,
        "macro_trend": "BULLISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=104.6)
    assert should_exit is False
    assert "VETO_HOLD" in reason
    assert meta.get("hold_rule") in ("VETO_EXIT_MA3_STRONG_UP", "TOP_LEVEL_TREND_LOCK_LONG")


def test_exit_gate_holds_short_when_ma3_sloping_down_with_lower_wick():
    """
    [防線一回歸測試] 空單下殺痛點：
    空單下殺破軌，當根碰觸 KC 下軌並收陰線帶下影線（但非極端探底針），
    MA3 依然強勁下殺且收盤受壓於 MA3 之下，ExitGate 必須 100% 行使 VETO_HOLD 續抱主跌浪！
    """
    bars = [
        make_bar(100.0, 100.2, 98.8, 99.0),
        make_bar(99.0, 99.2, 97.5, 97.8),
        make_bar(97.8, 98.0, 95.5, 96.0),
        # 當根：開 96.0，最低探破下軌 95.0 達 94.5，收 95.4。實體 0.6，下影線 0.9 (正常下影線，非探底針)
        make_bar(96.0, 96.2, 94.5, 95.4),
    ]
    df = make_df(*bars)
    pos = {
        "side": "SHORT",
        "entry_price": 100.0,
        "lowest_price": 94.5,
        "qty": 1.0,
        "leverage": 1.0,
    }
    snapshot = {
        "current_price": 95.4,
        "atr": ATR,
        "kc_upper": UP,
        "kc_middle": MID,
        "kc_lower": LOW,
        "kc_middle_slope": -0.02,
        "ema_fast": 97.0,
        "ema_slow": 99.0,
        "raw_df": df,
        "macro_trend": "BEARISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=95.4)
    assert should_exit is False
    assert "VETO_HOLD" in reason
    assert meta.get("hold_rule") in ("VETO_EXIT_MA3_STRONG_DOWN", "TOP_LEVEL_TREND_LOCK_SHORT", "VETO_HOLD_SHORT_BELOW_MA3")


def test_exit_gate_holds_short_when_single_green_bar_below_ma3():
    """
    [實盤 17:21 痛點回歸測試]
    情境重現：
    空單在大暴跌主跌浪進行途中，在 17:21 出現一根單根帶下影線的小陽線（綠 K），
    但收盤價依然受壓在紅色 MA3 之下 (close <= ma3)，且遠在 KC 中軌下方，中軌向下。
    ExitGate.evaluate 必須在最頂層 100% 一票否決提前離場，強制鎖倉續抱 (VETO_HOLD_SHORT_BELOW_MA3)！
    """
    bars = [
        make_bar(100.0, 100.2, 97.5, 98.0),
        make_bar(98.0, 98.2, 95.0, 95.5),
        make_bar(95.5, 95.6, 92.0, 92.5),   # 大暴跌破下軌 95.0
        # 17:21：單根帶下影線小綠棒，開 92.5，最低 91.0，最高 93.2，收 93.0
        # 此時 3 根 close 為 [95.5, 92.5, 93.0]，ma3 = (95.5 + 92.5 + 93.0) / 3 = 93.67
        # 收盤價 93.0 <= ma3 (93.67)，且遠在中軌 100.0 之下
        make_bar(92.5, 93.2, 91.0, 93.0),
    ]
    df = make_df(*bars)
    pos = {
        "side": "SHORT",
        "entry_price": 100.0,
        "lowest_price": 91.0,
        "qty": 1.0,
        "leverage": 5.0,
    }
    snapshot = {
        "current_price": 93.0,
        "atr": ATR,
        "kc_upper": UP,
        "kc_middle": MID,
        "kc_lower": LOW,
        "kc_middle_slope": -0.05,
        "ma15": 97.0,
        "ema_fast": 96.0,
        "ema_slow": 98.0,
        "raw_df": df,
        "macro_trend": "BEARISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=93.0)
    assert should_exit is False, "17:21 單根小綠棒受壓於 MA3 之下，必須 100% 阻斷平空，強制鎖倉！"
    assert "VETO_HOLD" in reason
    assert "VETO_HOLD_SHORT" in reason
    assert meta.get("hold_rule") == "VETO_HOLD_SHORT_BELOW_MA3"
    assert "價格壓制在MA3與KC中軌之下" in meta.get("cause", "") or "價格壓制在MA3與KC中軌之下" in reason


def test_exit_gate_holds_long_when_single_red_bar_above_ma3():
    """
    [多單衝高回踩痛點回歸測試]
    情境重現：
    多單在主升浪狂飆途中，出現一根單根帶上影線的小陰線（紅 K），
    但收盤價依然穩踩在紅色 MA3 之上 (close >= ma3)，且在 KC 中軌上方，中軌向上。
    ExitGate.evaluate 必須在最頂層 100% 一票否決提前離場，強制鎖倉續抱 (VETO_HOLD_LONG_ABOVE_MA3)！
    """
    bars = [
        make_bar(100.0, 102.5, 99.8, 102.0),
        make_bar(102.0, 105.0, 101.8, 104.5),
        make_bar(104.5, 108.0, 104.2, 107.5),  # 衝破上軌 105.0
        # 當根：單根帶上影線小陰棒，開 107.5，最高 109.0，最低 106.8，收 107.0
        # 此時 3 根 close 為 [104.5, 107.5, 107.0]，ma3 = (104.5 + 107.5 + 107.0) / 3 = 106.33
        # 收盤價 107.0 >= ma3 (106.33)，且穩守在中軌 100.0 之上
        make_bar(107.5, 109.0, 106.8, 107.0),
    ]
    df = make_df(*bars)
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 109.0,
        "qty": 1.0,
        "leverage": 5.0,
    }
    snapshot = {
        "current_price": 107.0,
        "atr": ATR,
        "kc_upper": UP,
        "kc_middle": MID,
        "kc_lower": LOW,
        "kc_middle_slope": 0.05,
        "ma15": 103.0,
        "ema_fast": 104.0,
        "ema_slow": 102.0,
        "raw_df": df,
        "macro_trend": "BULLISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=107.0)
    assert should_exit is False, "單根小陰棒守在 MA3 之上，必須 100% 阻斷平多，強制鎖倉！"
    assert "VETO_HOLD" in reason
    assert "VETO_HOLD_LONG" in reason
    assert meta.get("hold_rule") == "VETO_HOLD_LONG_ABOVE_MA3"
    assert "價格穩守在MA3與KC中軌之上" in meta.get("cause", "") or "價格穩守在MA3與KC中軌之上" in reason


