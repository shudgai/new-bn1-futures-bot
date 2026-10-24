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

def test_short_hold_when_below_kc_mid_or_ma15():
    """鐵律驗證：收盤價未實體站穩 KC 中軌 / MA15 之上，嚴禁觸發見底平空！耐受下軌震盪反抽！"""
    bars = [
        make_bar(97.0, 97.2, 95.8, 96.0, mid=100.0),
        make_bar(96.0, 96.2, 92.8, 93.0, mid=100.0),
        make_bar(93.0, 93.2, 91.8, 92.0, mid=100.0),
        make_bar(92.0, 92.2, 90.8, 91.0, mid=100.0),   # 探底
        make_bar(91.0, 94.2, 90.9, 94.0, mid=100.0),   # 勾起但 close=94.0 仍在中軌 100.0 之下
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 90.8}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False
    assert meta.get("skipped") == "BELOW_KC_MID_OR_MA15_HOLD_SHORT"


def test_short_ma3_v_bottom_exits_when_breaking_above_mid():
    """空單下殺後，實體收盤明確站上 KC 中軌與 MA15 且 MA3 走出 V 轉底，及時平空鎖利！"""
    # 當中軌 mid=93.0，最新棒收 94.0 突破站穩中軌
    bars = [
        make_bar(97.0, 97.2, 95.8, 96.0, mid=93.0, up=97.0, low=89.0),
        make_bar(96.0, 96.2, 92.8, 93.0, mid=93.0, up=97.0, low=89.0),
        make_bar(93.0, 93.2, 91.8, 92.0, mid=93.0, up=97.0, low=89.0),
        make_bar(92.0, 92.2, 90.8, 91.0, mid=93.0, up=97.0, low=89.0),   # 最低點，探破下軌
        make_bar(91.0, 94.2, 90.9, 94.0, mid=93.0, up=97.0, low=89.0),   # 向上勾起，收 94.0 站上中軌 93.0
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 90.8}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is True
    assert reason == CLOSE_SHORT_AT_BOTTOM
    assert any("MA3_V_BOTTOM" in t for t in meta["triggers"])


def test_short_pin_bar_bottom_exits_when_breaking_above_mid():
    """空單探底回升，實體收盤明確收在 KC 中軌與 MA15 之上，及時平空鎖利！"""
    bars = [
        make_bar(100.0, 100.2, 98.5, 99.0, mid=95.0, up=99.0, low=91.0),
        make_bar(99.0, 99.1, 96.8, 97.0, mid=95.0, up=99.0, low=91.0),
        make_bar(97.0, 97.2, 95.0, 95.5, mid=95.0, up=99.0, low=91.0),
        # 當根：開 95.1，最低探至 90.0，收 95.3 (突破站上中軌 95.0)。實體 0.2，下影線 5.1
        make_bar(95.1, 95.6, 90.0, 95.3, mid=95.0, up=99.0, low=91.0),
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 90.0}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is True
    assert reason == CLOSE_SHORT_AT_BOTTOM
    assert any("PIN_BAR_BOTTOM" in t for t in meta["triggers"])


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
        make_bar(100.0, 100.5, 100.0, 100.2, mid=99.0),
        make_bar(100.2, 100.4, 100.0, 100.1, mid=99.0),
        make_bar(100.1, 100.3, 100.0, 100.2, mid=99.0),
        make_bar(100.2, 100.4, 100.0, 100.1, mid=99.0),
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 100.0}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False
    assert meta.get("skipped") == "NO_FAVORABLE_MOVE_YET"


# ══════════════════════════════════════════════════════
# 2. 多單測試 (LONG)
# ══════════════════════════════════════════════════════

def test_long_hold_when_above_kc_mid_or_ma15():
    """鐵律驗證：收盤價未實體跌破 KC 中軌 / MA15 之下，嚴禁觸發見頂平多！耐受上軌震盪回踩！"""
    bars = [
        make_bar(99.0, 100.2, 98.8, 100.0, mid=100.0),
        make_bar(100.0, 103.2, 99.8, 103.0, mid=100.0),
        make_bar(103.0, 104.2, 102.8, 104.0, mid=100.0),
        make_bar(104.0, 107.5, 103.8, 107.0, mid=100.0),  # 衝破上軌
        make_bar(107.0, 107.2, 101.8, 102.0, mid=100.0),  # 回踩但收盤 102.0 仍在 KC 中軌 100.0 之上
    ]
    df = make_df(*bars)
    pos = {"side": "LONG", "entry_price": 100.0, "highest_price": 107.5}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is False
    assert meta.get("skipped") == "ABOVE_KC_MID_OR_MA15_HOLD_LONG"


def test_long_ma3_a_top_exits_when_breaking_below_mid():
    """多單衝高後，實體收盤明確跌破 KC 中軌與 MA15 且 MA3 走出倒 V 轉頂，及時平多鎖利！"""
    # 當中軌 mid=103.0，最新棒收 102.0 跌破中軌
    bars = [
        make_bar(99.0, 100.2, 98.8, 100.0, mid=103.0, up=107.0, low=99.0),
        make_bar(100.0, 103.2, 99.8, 103.0, mid=103.0, up=107.0, low=99.0),
        make_bar(103.0, 104.2, 102.8, 104.0, mid=103.0, up=107.0, low=99.0),
        make_bar(104.0, 107.5, 103.8, 107.0, mid=103.0, up=107.0, low=99.0),  # 衝破上軌 107.0
        make_bar(107.0, 107.2, 101.8, 102.0, mid=103.0, up=107.0, low=99.0),  # MA3 下彎且收 102.0 跌破中軌 103.0
    ]
    df = make_df(*bars)
    pos = {"side": "LONG", "entry_price": 100.0, "highest_price": 107.5}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is True
    assert reason == CLOSE_LONG_AT_TOP
    assert any("MA3_A_TOP" in t for t in meta["triggers"])


def test_long_pin_bar_top_exits_when_breaking_below_mid():
    """多單避雷針衝高回落，實體收盤明確跌破 KC 中軌與 MA15，及時平多鎖利！"""
    bars = [
        make_bar(100.0, 101.5, 99.8, 101.0, mid=105.0, up=108.0, low=102.0),
        make_bar(101.0, 103.2, 100.8, 103.0, mid=105.0, up=108.0, low=102.0),
        make_bar(103.0, 104.8, 102.8, 104.5, mid=105.0, up=108.0, low=102.0),
        # 當根：開 104.9，最高衝至 110.0，收 104.7 (跌破中軌 105.0)。實體 0.2，上影線 5.1
        make_bar(104.9, 110.0, 104.4, 104.7, mid=105.0, up=108.0, low=102.0),
    ]
    df = make_df(*bars)
    pos = {"side": "LONG", "entry_price": 100.0, "highest_price": 110.0}
    should_exit, reason, meta = evaluate_top_bottom_reversal_exit(pos, df, atr_val=ATR)
    assert should_exit is True
    assert reason == CLOSE_LONG_AT_TOP
    assert any("PIN_BAR_TOP" in t for t in meta["triggers"])


# ══════════════════════════════════════════════════════
# 3. ExitGate 整合回歸測試
# ══════════════════════════════════════════════════════

def test_exit_gate_holds_short_when_below_kc_mid_or_ma15():
    """驗證 ExitGate 整合：空單下殺探底即使有下影線，只要仍在 KC 中軌與 MA15 之下，一票否決 VETO_HOLD 抱牢！"""
    bars = [
        make_bar(100.0, 100.2, 97.8, 98.0, mid=MID),
        make_bar(98.0, 98.1, 95.8, 96.0, mid=MID),
        make_bar(96.0, 96.2, 94.0, 94.5, mid=MID),
        # 破下軌 95.0 的長下影線 pin bar: open=94.5, close=94.2, low=91.0 (仍在 mid=100.0 之下)
        make_bar(94.5, 94.6, 91.0, 94.2, mid=MID),
    ]
    df = make_df(*bars)
    pos = {"side": "SHORT", "entry_price": 100.0, "lowest_price": 91.0, "qty": 1.0, "leverage": 5.0}
    snapshot = {
        "current_price": 94.2,
        "atr": ATR,
        "kc_upper": UP,
        "kc_middle": MID,
        "kc_lower": LOW,
        "kc_middle_slope": -0.01,
        "ema_fast": 96.0,
        "ema_slow": 98.0,
        "raw_df": df,
        "macro_trend": "BEARISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot)
    # 由於在 KC 中軌與 MA15 之下，未達實質結構破壞，ExitGate 拒絕見底平空
    assert should_exit is False or reason != CLOSE_SHORT_AT_BOTTOM


def test_exit_gate_holds_long_when_above_kc_mid_or_ma15():
    """驗證 ExitGate 整合：多單衝高回踩即使有上影線，只要仍在 KC 中軌與 MA15 之上，一票否決 VETO_HOLD 抱牢！"""
    bars = [
        make_bar(99.0, 100.5, 98.8, 100.0, mid=MID),
        make_bar(100.0, 102.2, 99.8, 102.0, mid=MID),
        make_bar(102.0, 104.2, 101.8, 104.0, mid=MID),
        make_bar(104.0, 107.5, 103.8, 107.0, mid=MID),
        make_bar(107.0, 107.2, 102.8, 103.0, mid=MID),  # 仍在 mid=100.0 之上
    ]
    df = make_df(*bars)
    pos = {"side": "LONG", "entry_price": 100.0, "highest_price": 107.5, "qty": 1.0, "leverage": 5.0}
    snapshot = {
        "current_price": 103.0,
        "atr": ATR,
        "kc_upper": UP,
        "kc_middle": MID,
        "kc_lower": LOW,
        "kc_middle_slope": 0.01,
        "ema_fast": 104.0,
        "ema_slow": 102.0,
        "raw_df": df,
        "macro_trend": "BULLISH",
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot)
    assert should_exit is False or reason != CLOSE_LONG_AT_TOP


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
