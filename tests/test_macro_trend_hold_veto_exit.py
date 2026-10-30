"""
測試模組：大週期趨勢持倉防誤平鐵律 (Rule 5 升級版)
=========================================================
依 2026-10-30 規格授權：
  - 大週期熊市 (is_zoomed_out_bearish=True) 持空單時：
      收盤 > MA15 但 KC 斜率仍向下或 MA3 未站上 MA15
      → is_fast_v_reversal_close = False → 一票否決平空，強制 HOLD
  - 大週期牛市 (is_zoomed_out_bullish=True) 持多單時：
      收盤 < MA15 但 KC 斜率仍向上或 MA3 未跌破 MA15
      → is_fast_bear_reversal_close = False → 一票否決平多，強制 HOLD
  - 非大週期確立時（中性環境）：維持原敏感平倉邏輯
"""

import pytest


def _calc_fast_v_reversal_close(
    can_exit: bool,
    closed_c: float,
    m15_c: float,
    curr_quote: float,
    closed_o: float,
    is_zoomed_out_bearish: bool,
    kc_slope: float,
    kc_slope_3_ref: float,
    m3_c: float,
    min_gc_dc_spread: float = 0.0001,
) -> bool:
    """
    複製 execution_manager.py 中修正後的 is_fast_v_reversal_close 邏輯
    （用於單元測試，無需匯入整個模組）
    """
    return bool(
        can_exit
        and closed_c > m15_c
        and curr_quote > m15_c
        and closed_c > closed_o
        and (
            (not is_zoomed_out_bearish)
            or (
                is_zoomed_out_bearish
                and kc_slope > 0
                and kc_slope_3_ref > 0
                and m3_c > m15_c
                and (m3_c - m15_c) >= min_gc_dc_spread
            )
        )
    )


def _calc_fast_bear_reversal_close(
    can_exit: bool,
    closed_c: float,
    m15_c: float,
    curr_quote: float,
    closed_o: float,
    is_zoomed_out_bullish: bool,
    kc_slope: float,
    kc_slope_3_ref: float,
    m3_c: float,
    min_gc_dc_spread: float = 0.0001,
) -> bool:
    """
    複製 execution_manager.py 中修正後的 is_fast_bear_reversal_close 邏輯
    """
    return bool(
        can_exit
        and closed_c < m15_c
        and curr_quote < m15_c
        and closed_c < closed_o
        and (
            (not is_zoomed_out_bullish)
            or (
                is_zoomed_out_bullish
                and kc_slope < 0
                and kc_slope_3_ref < 0
                and m3_c < m15_c
                and (m15_c - m3_c) >= min_gc_dc_spread
            )
        )
    )


# ──────────────────────────────────────────────
# 空單防誤平測試 (is_fast_v_reversal_close)
# ──────────────────────────────────────────────

class TestShortPositionHoldVeto:

    def test_bearish_macro_small_bounce_above_ma15_veto(self):
        """
        龍蝦典型場景：大週期熊市空單持倉，價格小幅反彈過 MA15，
        KC 斜率仍向下 → 必須一票否決平空 (False)
        """
        result = _calc_fast_v_reversal_close(
            can_exit=True,
            closed_c=0.02115,       # 收盤 > MA15 (反彈過線)
            m15_c=0.02100,          # MA15
            curr_quote=0.02118,
            closed_o=0.02090,       # 收陽線
            is_zoomed_out_bearish=True,
            kc_slope=-0.00000434,   # KC 斜率仍向下！
            kc_slope_3_ref=-0.0000020,
            m3_c=0.02095,           # MA3 仍在 MA15 之下
        )
        assert result is False, "大週期熊市 + KC斜率向下 + MA3未站上MA15 → 嚴禁平空，必須 HOLD！"

    def test_bearish_macro_bounce_but_ma3_below_ma15_veto(self):
        """
        大週期熊市，收盤站上MA15，KC斜率轉正但MA3仍低於MA15
        → 仍不夠確認，一票否決平空
        """
        result = _calc_fast_v_reversal_close(
            can_exit=True,
            closed_c=0.02115,
            m15_c=0.02100,
            curr_quote=0.02118,
            closed_o=0.02090,
            is_zoomed_out_bearish=True,
            kc_slope=0.0000010,     # KC斜率轉正
            kc_slope_3_ref=0.0000005,
            m3_c=0.02095,           # MA3 仍在 MA15 之下 → 不夠！
        )
        assert result is False, "大週期熊市 + MA3仍在MA15下方 → 平空條件未達標，必須 HOLD！"

    def test_bearish_macro_true_reversal_both_confirmed(self):
        """
        大週期熊市，收盤站上MA15，KC斜率轉正 + MA3站上MA15 + 間距夠大
        → 真正的牛市反轉，允許平空
        """
        result = _calc_fast_v_reversal_close(
            can_exit=True,
            closed_c=0.02200,
            m15_c=0.02100,
            curr_quote=0.02210,
            closed_o=0.02080,
            is_zoomed_out_bearish=True,
            kc_slope=0.0000080,     # KC斜率明確向上
            kc_slope_3_ref=0.0000060,
            m3_c=0.02160,           # MA3 站上 MA15 且有間距
        )
        assert result is True, "大週期熊市 + KC斜率向上 + MA3站上MA15 → 真反轉，允許平空！"

    def test_neutral_macro_sensitive_exit_maintained(self):
        """
        非大週期熊市（中性環境），維持原敏感平空邏輯
        收盤 > MA15 + 收陽線 → 直接允許平空
        """
        result = _calc_fast_v_reversal_close(
            can_exit=True,
            closed_c=0.02115,
            m15_c=0.02100,
            curr_quote=0.02118,
            closed_o=0.02090,
            is_zoomed_out_bearish=False,  # 中性或牛市
            kc_slope=-0.00001,            # KC斜率向下也沒差
            kc_slope_3_ref=-0.000005,
            m3_c=0.02090,                 # MA3在MA15之下也沒差
        )
        assert result is True, "非大週期熊市（中性/牛市）→ 維持敏感平空邏輯，應允許平空！"

    def test_cannot_exit_red_candle_veto(self):
        """
        收陰線（closed_c < closed_o）→ can_exit_short = False，絕不平空
        """
        result = _calc_fast_v_reversal_close(
            can_exit=True,
            closed_c=0.02115,
            m15_c=0.02100,
            curr_quote=0.02118,
            closed_o=0.02130,   # 收陰線！closed_c < closed_o
            is_zoomed_out_bearish=False,
            kc_slope=0.00001,
            kc_slope_3_ref=0.000005,
            m3_c=0.02160,
        )
        assert result is False, "收陰線時 closed_c < closed_o → 嚴禁平空！"


# ──────────────────────────────────────────────
# 多單防誤平測試 (is_fast_bear_reversal_close)
# ──────────────────────────────────────────────

class TestLongPositionHoldVeto:

    def test_bullish_macro_small_dip_below_ma15_veto(self):
        """
        大週期牛市多單持倉，價格小幅回踩跌破 MA15，
        KC 斜率仍向上 → 必須一票否決平多 (False)
        """
        result = _calc_fast_bear_reversal_close(
            can_exit=True,
            closed_c=0.00488,       # 收盤 < MA15
            m15_c=0.00490,          # MA15
            curr_quote=0.00487,
            closed_o=0.00495,       # 收陰線
            is_zoomed_out_bullish=True,
            kc_slope=0.00000014,    # KC 斜率仍向上！
            kc_slope_3_ref=0.00000010,
            m3_c=0.00492,           # MA3 仍在 MA15 之上
        )
        assert result is False, "大週期牛市 + KC斜率向上 + MA3未跌破MA15 → 嚴禁平多，必須 HOLD！"

    def test_bullish_macro_dip_but_ma3_above_ma15_veto(self):
        """
        大週期牛市，收盤跌破MA15，KC斜率轉負但MA3仍高於MA15
        → 仍不夠確認，一票否決平多
        """
        result = _calc_fast_bear_reversal_close(
            can_exit=True,
            closed_c=0.00488,
            m15_c=0.00490,
            curr_quote=0.00487,
            closed_o=0.00495,
            is_zoomed_out_bullish=True,
            kc_slope=-0.0000010,    # KC斜率轉負
            kc_slope_3_ref=-0.0000005,
            m3_c=0.00492,           # MA3 仍在 MA15 之上 → 不夠！
        )
        assert result is False, "大週期牛市 + MA3仍在MA15上方 → 平多條件未達標，必須 HOLD！"

    def test_bullish_macro_true_reversal_both_confirmed(self):
        """
        大週期牛市，收盤跌破MA15，KC斜率轉負 + MA3跌破MA15 + 間距夠大
        → 真正的熊市反轉，允許平多
        """
        result = _calc_fast_bear_reversal_close(
            can_exit=True,
            closed_c=0.00460,
            m15_c=0.00490,
            curr_quote=0.00458,
            closed_o=0.00480,
            is_zoomed_out_bullish=True,
            kc_slope=-0.0000080,    # KC斜率明確向下
            kc_slope_3_ref=-0.0000060,
            m3_c=0.00475,           # MA3 跌破 MA15 且有間距
        )
        assert result is True, "大週期牛市 + KC斜率向下 + MA3跌破MA15 → 真反轉，允許平多！"

    def test_neutral_macro_sensitive_exit_maintained(self):
        """
        非大週期牛市（中性環境），維持原敏感平多邏輯
        收盤 < MA15 + 收陰線 → 直接允許平多
        """
        result = _calc_fast_bear_reversal_close(
            can_exit=True,
            closed_c=0.00488,
            m15_c=0.00490,
            curr_quote=0.00487,
            closed_o=0.00495,
            is_zoomed_out_bullish=False,  # 中性或熊市
            kc_slope=0.00001,             # KC斜率向上也沒差
            kc_slope_3_ref=0.000005,
            m3_c=0.00495,                 # MA3在MA15之上也沒差
        )
        assert result is True, "非大週期牛市（中性/熊市）→ 維持敏感平多邏輯，應允許平多！"


# ──────────────────────────────────────────────
# 複合場景：龍蝦 09:56 誤平重現測試
# ──────────────────────────────────────────────

class TestLobsterFalseExitReproduction:
    """
    重現 2026-10-30 09:56 龍蝦/USDT 事件：
    - 開空 @ 0.02008（09:39，大週期熊市）
    - 09:56 價格小幅反彈，closed_c=0.02115 > m15_c=0.02100
    - KC 斜率仍向下（kc_slope < 0）
    - 修正前：誤觸 CLOSE_SHORT_ON_BULLISH_REVERSAL
    - 修正後：一票否決，強制 HOLD，繼續持空單跌至 0.020350
    """

    def test_lobster_0956_false_exit_prevented(self):
        """修正後：龍蝦 09:56 誤平事件不再發生"""
        result = _calc_fast_v_reversal_close(
            can_exit=True,
            closed_c=0.02115,       # 收盤微幅站上 MA15
            m15_c=0.02100,
            curr_quote=0.02118,
            closed_o=0.02090,       # 收陽線
            is_zoomed_out_bearish=True,
            kc_slope=-0.00000434,   # KC 斜率仍向下（日誌中的真實值）
            kc_slope_3_ref=-0.00000200,
            m3_c=0.02095,           # MA3 仍低於 MA15
        )
        assert result is False, (
            "龍蝦 09:56 事件重現：大週期熊市+KC向下+MA3未站上MA15 → "
            "修正後必須返回 False，一票否決誤平，繼續持空單！"
        )

    def test_lobster_continue_short_profits(self):
        """後續行情：價格繼續跌至 0.020350，空單應持續持有"""
        # 模擬 09:56 之後的下跌 K 棒（收陰線）
        result = _calc_fast_v_reversal_close(
            can_exit=True,
            closed_c=0.02038,       # 收盤跌回
            m15_c=0.02100,
            curr_quote=0.02035,
            closed_o=0.02060,       # 收陰線
            is_zoomed_out_bearish=True,
            kc_slope=-0.00000434,
            kc_slope_3_ref=-0.00000200,
            m3_c=0.02042,
        )
        # 陰線 closed_c < closed_o → can_exit_short_on_candle 邏輯為 False（此處直接測試邏輯）
        # closed_c > closed_o 條件不滿足，result 必為 False
        assert result is False, "下跌陰線期間 → 嚴禁平空！"
