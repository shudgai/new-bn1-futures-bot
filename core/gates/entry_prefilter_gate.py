"""
EntryPreFilterGate (進場前置過濾守衛)

徹底封鎖兩類無效與虧損開倉：
1. 嚴禁在生命線下方逆勢摸多 (Close < KC_mid 或 Close < MA15，或 MA15 向下傾斜)
2. 嚴禁在通道走平、收斂震盪區追單 (kc_width < 1.5 * atr，或 MA15/KC中軌走平無趨勢時追空)
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple


def can_execute_entry(
    signal_type: str,
    candle: Dict[str, Any],
    kc: Dict[str, Any],
    ma5: float,
    ma15: float,
    kc_mid_slope: float,
    ma15_slope: float,
    avg_atr: float,
    entry_pattern: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    開倉前置過濾守衛：過濾逆勢摸單與震盪假突破
    :param signal_type: 'LONG' 或 'SHORT'
    :param candle: 當前 K 棒數據字典，包含 open, high, low, close
    :param kc: 肯特納通道字典，包含 upper, lower, mid
    :param ma5: 當前 MA5 數值
    :param ma15: 當前 MA15 數值
    :param kc_mid_slope: KC 中軌斜率 (當前中軌 - 前一根中軌)
    :param ma15_slope: MA15 均線斜率 (當前 MA15 - 前一根 MA15)
    :param avg_atr: 平均 ATR 幅度
    :param entry_pattern: 開倉型態 (如 BREAKOUT, CONTINUATION 等)
    :return: (is_allowed: bool, reason: str)
    """
    signal_type = str(signal_type).upper()
    if signal_type not in ("LONG", "SHORT"):
        return False, "UNKNOWN_SIGNAL_TYPE"

    try:
        close_price = float(candle["close"])
        kc_upper = float(kc.get("upper", kc.get("kc_upper", 0.0)))
        kc_lower = float(kc.get("lower", kc.get("kc_lower", 0.0)))
        kc_mid = float(kc.get("mid", kc.get("kc_middle", 0.0)))
        ma15_val = float(ma15)
        mid_slope = float(kc_mid_slope)
        m15_slope = float(ma15_slope)
        atr_val = float(avg_atr) if float(avg_atr) > 0 else 0.0001
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, "VETO_INVALID_PREFILTER_DATA: 參數無效或缺失"

    pattern_str = str(entry_pattern or "").upper()
    is_outer_breakout = (
        any(p in pattern_str for p in ("BREAKOUT", "CONTINUATION", "OUTER", "DIP_SNIPER", "MA_CROSS"))
        or (signal_type == "LONG" and close_price > kc_upper)
        or (signal_type == "SHORT" and close_price < kc_lower)
    )

    kc_width = kc_upper - kc_lower

    # =========================================================================
    # 防護 1：通道收斂與走平過濾 (過濾震盪泥淖)
    # 若通道開口太窄 (小於基準 ATR 的 1.5 倍)，代表處於盤整泥淖。
    # 破軌、延續、波段起步與高拋低吸開倉因價格展開動能，擠壓即為起跑發動點，不在此限。
    # =========================================================================
    if not is_outer_breakout and kc_width < (atr_val * 1.5):
        return False, f"BLOCKED_ENTRY_KC_CHANNEL_SQUEEZE_CHOP: KC 通道嚴重收斂 (width={kc_width:.5f} < 1.5*ATR={1.5*atr_val:.5f})，處於無趨勢橫盤震盪，嚴禁開倉！"

    # =========================================================================
    # 防護 2：多單禁止在生命線下方接刀 (過濾逆勢買多)
    # =========================================================================
    if signal_type == "LONG":
        # 價格必須明確站在 KC 中軌與 MA15 上方
        if close_price < kc_mid or close_price < ma15_val:
            return False, f"BLOCKED_LONG_BELOW_MIDLINE_OR_MA15: 價格 ({close_price}) 受壓於 KC 中軌 ({kc_mid}) 或 MA15 ({ma15_val}) 之下，嚴禁逆勢摸多！"
        # 趨勢方向：KC 中軌不能向下
        if mid_slope < -0.0001:
            return False, f"BLOCKED_LONG_ON_BEARISH_TREND: KC 中軌向下 (slope={mid_slope:.6f})，禁止逆勢做多！"
        # 非破軌/波段起漲時，MA15 不能向下傾斜
        if not is_outer_breakout and m15_slope < 0:
            return False, f"BLOCKED_LONG_MA15_SLOPE_DOWN: MA15 均線仍向下傾斜 (slope={m15_slope:.6f} < 0)，多頭結構未確認，禁止做多！"

    # =========================================================================
    # 防護 3：空單禁止在均線走平時追空 (防假跌破)
    # =========================================================================
    elif signal_type == "SHORT":
        # 價格必須受壓於 KC 中軌與 MA15 下方
        if close_price > kc_mid or close_price > ma15_val:
            return False, f"BLOCKED_SHORT_ABOVE_MIDLINE_OR_MA15: 價格 ({close_price}) 高於 KC 中軌 ({kc_mid}) 或 MA15 ({ma15_val})，嚴禁逆勢做空！"
        # 趨勢方向：KC 中軌走平或向上時嚴禁做空 (起跌與狙擊豁免滯後中軌)
        if not is_outer_breakout and mid_slope >= 0:
            return False, f"BLOCKED_SHORT_KC_MID_FLAT_OR_UP: KC 中軌走平或向上 (slope={mid_slope:.6f}) 無下傾趨勢，禁止追空！"
        # 非破軌/波段起跌開倉時，MA15 走平或向上嚴禁追空
        if not is_outer_breakout and m15_slope >= 0:
            return False, f"BLOCKED_SHORT_MA15_FLAT_OR_UP: MA15 走平或向上 (slope={m15_slope:.6f}) 無下傾趨勢，禁止追空！"

    return True, "PASSED_ENTRY_PREFILTER: 通過過濾，允許順勢開倉"


class EntryPreFilterGate:
    """Entry pre-filter gate providing static and class methods for entry validation."""

    @classmethod
    def evaluate(
        cls,
        signal_type: str,
        candle: Dict[str, Any],
        indicators: Dict[str, Any],
        entry_pattern: Optional[str] = None,
    ) -> Tuple[bool, str]:
        kc = {
            "upper": indicators.get("kc_upper", 0.0),
            "lower": indicators.get("kc_lower", 0.0),
            "mid": indicators.get("kc_middle", indicators.get("kc_basis", 0.0)),
        }
        ma5 = float(indicators.get("ma5", 0.0))
        ma15 = float(indicators.get("ma15", 0.0))
        kc_mid_slope = float(indicators.get("kc_middle_slope", indicators.get("kc_mid_slope", 0.0)))
        ma15_slope = float(indicators.get("ma15_slope", 0.0))
        avg_atr = float(indicators.get("atr", 0.0001))

        return can_execute_entry(
            signal_type=signal_type,
            candle=candle,
            kc=kc,
            ma5=ma5,
            ma15=ma15,
            kc_mid_slope=kc_mid_slope,
            ma15_slope=ma15_slope,
            avg_atr=avg_atr,
            entry_pattern=entry_pattern,
        )
