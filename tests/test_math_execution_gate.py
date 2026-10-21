import pandas as pd

from core.services.math_execution_gate import validate_entry_math, validate_exit_math


def _frame(**current_overrides):
    bars = [
        dict(timestamp=60_000, open=100.0, high=101.0, low=99.0, close=100.0,
             ma5=100.0, ma15=100.5, atr=2.0, kc_upper=105.0, kc_middle=100.0,
             kc_lower=95.0),
        dict(timestamp=120_000, open=100.0, high=101.0, low=99.0, close=100.0,
             ma5=100.0, ma15=101.0, atr=2.0, kc_upper=105.0, kc_middle=101.0,
             kc_lower=95.0),
    ]
    bars[-2].update(ma5=99.0)
    bars[-1].update(current_overrides)
    return pd.DataFrame(bars)


def test_entry_math_alignment_chase_and_wick_veto():
    frame = _frame(ma5=102, ma15=101, close=102, open=101.5, high=102.2)
    assert validate_entry_math(frame, "LONG")[0]
    frame.loc[1, ["close", "high"]] = [104, 104.2]
    assert validate_entry_math(frame, "LONG")[1] == "BLOCKED_ENTRY_MATH_LONG_CHASE"
    frame = _frame(ma5=102, ma15=101, close=102, open=101.5, high=103)
    assert validate_entry_math(frame, "LONG")[1] == "BLOCKED_ENTRY_MATH_LONG_WICK"


def test_outer_rail_breakout_and_continuation_allows_entry():
    # Outer rail long breakout: close > kc_upper (107 > 105), distance <= 3 ATR
    frame = _frame(ma5=106, ma15=101, close=107, open=105.5, high=107.2, kc_upper=105, kc_middle=101, atr=2)
    assert validate_entry_math(frame, "LONG") == (True, "ENTRY_MATH_APPROVED")

    # Long outer chase over 3 ATR: close - kc_upper = 112 - 105 = 7 > 3 * 2 (6)
    frame.loc[1, ["close", "high"]] = [112, 112.2]
    assert validate_entry_math(frame, "LONG") == (False, "BLOCKED_ENTRY_MATH_LONG_CHASE")

    # Long outer with MA5 downturn
    frame.loc[1, ["close", "high", "ma5"]] = [107, 107.2, 98]
    assert validate_entry_math(frame, "LONG") == (False, "BLOCKED_ENTRY_MATH_LONG_MA_ALIGNMENT")

    # Outer rail short breakout: close < kc_lower (93 < 95), distance <= 3 ATR
    short_frame = _frame(ma5=94, ma15=99, close=93, open=94.5, low=92.8, high=94.6, kc_lower=95, kc_middle=99, atr=2)
    short_frame.loc[0, "kc_middle"] = 100
    assert validate_entry_math(short_frame, "SHORT") == (True, "ENTRY_MATH_APPROVED")

    # Short outer with MA5 >= MA15 (countertrend MA alignment)
    short_bad_ma = short_frame.copy()
    short_bad_ma.loc[1, "ma5"] = 99.5
    assert validate_entry_math(short_bad_ma, "SHORT") == (False, "BLOCKED_ENTRY_MATH_SHORT_MA_ALIGNMENT")

    # Short outer with MA5 and MA15 glued together (Chop)
    short_chop = short_frame.copy()
    short_chop.loc[1, ["ma5", "ma15"]] = [94.02, 94.0]
    assert validate_entry_math(short_chop, "SHORT") == (False, "BLOCKED_ENTRY_MATH_MA_TOO_CLOSE")


def test_profit_lock_fast_track_requires_matching_outer_rail_candle():
    frame = _frame(open=101, close=106, high=106.2, ma5=102, ma15=101)
    assert validate_entry_math(frame, "LONG", profit_lock_fast_track=True)[0]
    frame.loc[1, "close"] = 104
    assert not validate_entry_math(frame, "LONG", profit_lock_fast_track=True)[0]


def test_kc_midline_trend_vetoes_countertrend_and_cannot_be_bypassed():
    bearish = _frame(
        open=100, high=101, low=99, close=100,
        ma5=102, ma15=101, kc_middle=99,
    )
    assert validate_entry_math(bearish, "LONG") == (
        False, "BLOCKED_LONG_ON_BEARISH_TREND",
    )
    assert validate_entry_math(
        bearish, "LONG", profit_lock_fast_track=True,
    ) == (False, "BLOCKED_LONG_ON_BEARISH_TREND")

    bullish = _frame(
        open=100, high=101, low=99, close=100,
        ma5=98, ma15=99, kc_middle=101,
    )
    assert validate_entry_math(bullish, "SHORT") == (
        False, "BLOCKED_SHORT_ON_BULLISH_TREND",
    )

    flat = _frame(kc_middle=100)
    assert validate_entry_math(flat, "LONG")[1] == "BLOCKED_ENTRY_MATH_KC_TREND_FLAT"


def test_execution_entry_validation_prefers_fresh_market_frame_over_signal_cache():
    import asyncio

    from core.services.math_execution_gate import validate_entry_at_execution

    stale_bullish = _frame(
        open=100, high=101, low=99, close=100,
        ma5=102, ma15=101, kc_middle=101,
    )
    fresh_bearish = _frame(
        open=100, high=101, low=99, close=100,
        ma5=102, ma15=101, kc_middle=99,
    )

    class Account:
        entry_frame_provider = staticmethod(lambda _symbol: fresh_bearish)

    allowed, reason = asyncio.run(validate_entry_at_execution(
        Account(), "CAP/USDT", "LONG", {"frame": stale_bullish},
    ))
    assert not allowed
    assert reason == "BLOCKED_LONG_ON_BEARISH_TREND"


def test_exit_math_true_peak_and_middle_trend_hold_veto():
    position = dict(side="LONG", entry_price=100, qty=1, margin=100,
                    highest_price=106, lowest_price=100)
    frame = _frame(open=105, high=106, low=104, close=105,
                   ma5=104, atr=2, kc_upper=105, kc_middle=103,
                   kc_lower=95)
    allowed, _, metadata = validate_exit_math(position, frame, 104.5)
    assert allowed and metadata["exit_type"] == "TRUE_PEAK"
    frame.loc[1, "kc_upper"] = 107
    allowed, reason, metadata = validate_exit_math(position, frame, 105)
    assert not allowed and reason == "VETO_HOLD"
    assert metadata["exit_type"] == "VETO_HOLD"


def test_exit_math_ratchet_and_emergency_override():
    position = dict(side="LONG", entry_price=100, qty=1, margin=100,
                    highest_price=110, lowest_price=100,
                    profit_lock_state={"peak_net_roe_pct": 10, "floor_net_roe_pct": 7})
    frame = _frame(open=109, high=110, low=107, close=109,
                   ma5=108, atr=2, kc_upper=112, kc_middle=105,
                   kc_lower=95)
    allowed, _, metadata = validate_exit_math(position, frame, 106)
    assert allowed and metadata["exit_type"] == "PROFIT_LOCK"
    allowed, reason, metadata = validate_exit_math(position, frame, 90,
                                                   close_reason="CIRCUIT_BREAKER_HARD_SL")
    assert allowed and reason == "EMERGENCY_EXIT_OVERRIDE"
    assert metadata["exit_type"] == "EMERGENCY"


def test_exit_math_confirmed_midline_reversal():
    # Short position where price breaks back above KC middle
    short_pos = dict(side="SHORT", entry_price=0.0702, qty=1000, margin=15,
                     lowest_price=0.0694, highest_price=0.0702)
    frame = _frame(open=0.0700, high=0.0702, low=0.0698, close=0.0701,
                   ma5=0.0700, atr=0.0002, kc_upper=0.0706, kc_middle=0.0699,
                   kc_lower=0.0692)
    allowed, reason, metadata = validate_exit_math(
        short_pos, frame, 0.0701,
        close_reason="Wild Swing EXIT_BY_SHORT_CONFIRMED_KC_MIDLINE_REVERSAL"
    )
    assert allowed
    assert reason == "EXIT_MATH_SHORT_MIDLINE_REVERSAL"
    assert metadata["exit_type"] == "MIDLINE_REVERSAL"

    # Long position where price breaks back below KC middle
    long_pos = dict(side="LONG", entry_price=0.0700, qty=1000, margin=15,
                    highest_price=0.0708, lowest_price=0.0700)
    frame_long = _frame(open=0.0700, high=0.0701, low=0.0697, close=0.0698,
                        ma5=0.0699, atr=0.0002, kc_upper=0.0706, kc_middle=0.0699,
                        kc_lower=0.0692)
    allowed_l, reason_l, metadata_l = validate_exit_math(
        long_pos, frame_long, 0.0698,
        close_reason="Wild Swing EXIT_BY_LONG_CONFIRMED_KC_MIDLINE_REVERSAL"
    )
    assert allowed_l
    assert reason_l == "EXIT_MATH_LONG_MIDLINE_REVERSAL"
    assert metadata_l["exit_type"] == "MIDLINE_REVERSAL"


def test_entry_short_bearish_engulfing_reversal():
    """
    [規則 1 單元測試：假突破/長黑吞噬反轉開空]
    前一根為長紅 (open=100, close=104)，當前為長黑 (open=104, close=99.2)，
    收盤價直接跌破前根開盤價 100，且收盤低於 MA15 (101)。
    驗證滿足時精準放行 ENTRY_SHORT_ENGULFING_REVERSAL，未跌破開盤價時一票否決。
    """
    bars = [
        dict(timestamp=60_000, open=100.0, high=104.5, low=99.8, close=104.0,
             ma5=102.0, ma15=101.2, atr=2.0, kc_upper=105.0, kc_middle=100.5, kc_lower=95.0),
        dict(timestamp=120_000, open=104.0, high=104.2, low=99.0, close=99.2,
             ma5=101.5, ma15=101.0, atr=2.0, kc_upper=105.0, kc_middle=100.3, kc_lower=95.0),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="SHORT")
    assert allowed is True
    assert reason == "ENTRY_SHORT_ENGULFING_REVERSAL"

    # 沒跌破前開盤價 (收 100.5 > 100.0) -> 維持一票否決
    frame.loc[1, ["close", "low"]] = [100.5, 100.2]
    allowed_fail, reason_fail = validate_entry_math(frame, side="SHORT")
    assert allowed_fail is False
    assert "BLOCKED" in reason_fail


def test_entry_short_mid_band_break():
    """
    [規則 2 單元測試：破中軌 + 均線下拐提早進場開空]
    當前收盤價跌破 KC 中軌 (close=99.5 < kc_middle=100.0)，
    且 MA15 均線斜率向下 (curr_ma15=100.5 < prev_ma15=101.0)，
    驗證滿足時回傳 (True, 'ENTRY_SHORT_MID_BAND_BREAK')，
    若收盤價在中軌之上則維持一票否決。
    """
    bars = [
        dict(timestamp=60_000, open=101.0, high=101.5, low=100.0, close=100.5,
             ma5=101.0, ma15=101.0, atr=2.0, kc_upper=105.0, kc_middle=100.2, kc_lower=95.0),
        dict(timestamp=120_000, open=100.5, high=100.6, low=99.2, close=99.5,
             ma5=100.0, ma15=100.5, atr=2.0, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="SHORT")
    assert allowed is True
    assert reason == "ENTRY_SHORT_MID_BAND_BREAK"

    # 若未破中軌 (收 100.2 > kc_middle 100.0) -> 一票否決
    frame.loc[1, ["close", "high"]] = [100.2, 100.8]
    allowed_fail, reason_fail = validate_entry_math(frame, side="SHORT")
    assert allowed_fail is False
    assert "BLOCKED" in reason_fail


def test_three_point_pivot_vs_pullback_unit_test():
    """
    [第一階段：純幾何單元測試 —— 判定三點確認樞紐 vs 普通回踩]
    1. 多頭回踩均線不破：
       上升趨勢中出現小陰線拉回至 KC 中軌之上 (close=104 > middle=103)，
       中軌斜率向上 (103 > 102.5)，未觸發真頂部，驗證必須輸出 VETO_HOLD 拒絕提早平倉。
    2. 三棒形成確認頂部且收破支撐 (Three-Point Confirmed Pivot)：
       左側棒 High=108, 頂部棒 High=112 (極值高點), 右側確認棒 High=107 且收盤跌破 MA5 (close=104 < ma5=106)，
       驗證放行出場 (TRUE_PEAK / CONFIRMED_PIVOT)，不再被 VETO_HOLD 阻擋。
    """
    pos = dict(side="LONG", entry_price=100.0, qty=1.0, margin=50.0,
               highest_price=106.0, lowest_price=100.0)

    # 用例 1：多頭回踩均線不破 (上升趨勢中正常呼吸)
    pullback_bars = [
        dict(timestamp=60_000, open=103.0, high=105.0, low=102.5, close=104.5,
             ma5=104.0, ma15=102.0, atr=2.0, kc_upper=108.0, kc_middle=102.5, kc_lower=96.0),
        dict(timestamp=120_000, open=104.5, high=105.0, low=103.8, close=104.0,
             ma5=104.0, ma15=102.5, atr=2.0, kc_upper=108.0, kc_middle=103.0, kc_lower=96.0),
    ]
    frame_pullback = pd.DataFrame(pullback_bars)
    allowed, reason, meta = validate_exit_math(pos, frame_pullback, price=104.0)
    assert allowed is False, "正常多頭回踩均線未破時，必須拒絕平倉"
    assert reason == "VETO_HOLD"
    assert meta.get("exit_type") == "VETO_HOLD"

    # 用例 2：三棒形成確定性頂部樞紐 (High: 108 -> 112 -> 107, 右側確認棒長黑跌破 MA5 與支撐)
    pivot_bars = [
        dict(timestamp=60_000, open=105.0, high=108.0, low=104.0, close=107.0,
             ma5=106.0, ma15=103.0, atr=2.0, kc_upper=110.0, kc_middle=104.0, kc_lower=98.0),
        dict(timestamp=120_000, open=107.0, high=112.0, low=106.5, close=111.0,
             ma5=108.0, ma15=104.0, atr=2.0, kc_upper=111.0, kc_middle=104.5, kc_lower=98.0),
        dict(timestamp=180_000, open=110.0, high=110.2, low=103.5, close=104.0,
             ma5=106.0, ma15=104.5, atr=2.0, kc_upper=111.0, kc_middle=104.5, kc_lower=98.0),
    ]
    frame_pivot = pd.DataFrame(pivot_bars)
    pos_top = dict(side="LONG", entry_price=100.0, qty=1.0, margin=50.0,
                   highest_price=112.0, lowest_price=100.0)
    allowed_pivot, reason_pivot, meta_pivot = validate_exit_math(
        pos_top, frame_pivot, price=104.0,
        close_reason="CONFIRMED_TRUE_PEAK_PIVOT"
    )
    assert allowed_pivot is True, "三點樞紐確立且確認棒收破支撐時，必須授權平倉"
    assert reason_pivot in ("EXIT_MATH_CONFIRMED_PIVOT", "EXIT_MATH_TRUE_PEAK", "EXIT_MATH_EARLY_PIVOT")
    assert meta_pivot.get("exit_type") in ("CONFIRMED_PIVOT", "TRUE_PEAK", "EARLY_PIVOT")


def test_counter_trend_flip_long_blocked_on_bearish_kc():
    """
    [逆勢反手開倉門禁測試]
    在 KC 中軌向下的明確空頭走勢中 (prior_middle > latest_middle)，
    即便盤中出現大陽線突破均線，validate_entry_math(df, side="LONG") 必須 100% 一票否決，
    返回 BLOCKED_LONG_ON_BEARISH_TREND，杜絕在空頭主浪中摸底接飛刀與逆勢反手！
    """
    bars = [
        dict(timestamp=60_000, open=100.0, high=100.5, low=99.0, close=99.5,
             ma5=99.5, ma15=100.5, atr=2.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0),
        dict(timestamp=120_000, open=99.5, high=103.0, low=99.3, close=102.5,
             ma5=101.0, ma15=100.8, atr=2.0, kc_upper=103.5, kc_middle=99.5, kc_lower=95.5),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="LONG")
    assert allowed is False
    assert reason == "BLOCKED_LONG_ON_BEARISH_TREND"


def test_exit_short_on_ema_reversal():
    """
    [EMA 均線翻揚止盈單元測試]
    驗證當紅色快速均線 (EMA15/MA15) 斜率向上 (curr_ma15 > prev_ma15)
    且連續 2 根收盤價站在 EMA15 之上時，數學門禁 (validate_exit_math)
    必須放行平空訊號 (EXIT_MATH_EMA_REVERSAL)。
    """
    bars = [
        dict(timestamp=60_000, open=98.0, high=98.5, low=97.0, close=97.5,
             ma5=97.2, ma15=97.0, atr=2.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0),
        dict(timestamp=120_000, open=97.5, high=98.8, low=97.4, close=98.5,
             ma5=98.0, ma15=97.8, atr=2.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0),
    ]
    frame = pd.DataFrame(bars)
    pos = dict(side="SHORT", entry_price=100.0, qty=1.0, margin=50.0,
               highest_price=100.0, lowest_price=96.0)
    allowed, reason, details = validate_exit_math(
        pos, frame, price=98.5, close_reason="SHORT_EXIT_EMA_REVERSAL"
    )
    assert allowed is True
    assert reason == "EXIT_MATH_EMA_REVERSAL"
    assert details.get("exit_type") == "EMA_REVERSAL"


def test_entry_long_on_ema_cross_above_mid_band():
    """
    [EMA 金叉 + 站上 KC 中軌提早開多單元測試]
    驗證當 EMA15 金叉 EMA40 (ma15 >= ema_slow) 且 1m K 棒收在 KC 中軌上方 (close > kc_middle)
    且 EMA15 斜率向上發散時，validate_entry_math(df, side="LONG") 必須回傳 allowed == True，
    原因為 ENTRY_LONG_EMA_CROSS_MID，不再強制要求價格衝破 KC 上軌追高！
    """
    bars = [
        dict(timestamp=60_000, open=99.0, high=99.8, low=98.8, close=99.5,
             ma5=99.2, ma15=99.0, ema_slow=99.2, atr=2.0, kc_upper=104.0, kc_middle=99.0, kc_lower=95.0),
        dict(timestamp=120_000, open=99.5, high=100.8, low=99.4, close=100.5,
             ma5=99.8, ma15=99.6, ema_slow=99.3, atr=2.0, kc_upper=104.0, kc_middle=99.2, kc_lower=95.0),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="LONG")
    assert allowed is True
    assert reason == "ENTRY_LONG_EMA_CROSS_MID"


def test_exit_math_detects_early_confirmed_pivot_valley():
    """
    [谷底形態識別單元測試]
    驗證空頭行情下出現 3 棒確認谷底樞紐：
    第 1 根：長黑破底 (Low=98.0)
    第 2 根：插針最低點 (Low=96.5, 嚴格低於前後棒)
    第 3 根：收紅實體衝過前棒實體中軸 (Close=98.2 > mid2=97.5)
    validate_exit_math 必須回傳 allowed == True，且 exit_type 為 EARLY_PIVOT。
    """
    bars = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=98.0, close=98.5,
             ma5=99.0, ma15=100.0, atr=2.0, kc_upper=104.0, kc_middle=99.5, kc_lower=95.0),
        dict(timestamp=120_000, open=98.5, high=98.6, low=96.5, close=97.0,
             ma5=98.0, ma15=99.5, atr=2.0, kc_upper=104.0, kc_middle=99.5, kc_lower=95.0),
        dict(timestamp=180_000, open=97.0, high=98.5, low=96.8, close=98.2,
             ma5=97.5, ma15=99.0, atr=2.0, kc_upper=104.0, kc_middle=99.5, kc_lower=95.0),
    ]
    frame = pd.DataFrame(bars)
    pos = dict(side="SHORT", entry_price=100.0, qty=1.0, margin=50.0,
               highest_price=100.0, lowest_price=96.5)
    allowed, reason, details = validate_exit_math(
        pos, frame, price=98.2, close_reason="EARLY_PIVOT_VALLEY"
    )
    assert allowed is True
    assert details.get("exit_type") == "EARLY_PIVOT"


def test_entry_math_allows_early_ema_crossover_at_mid_band():
    """
    [EMA 交叉 + 中軌過濾提早開倉單元測試 (Unit Test)]
    驗證：
    1. 多單 (LONG)：EMA15 向上穿越 EMA40 (ma15 > ema_slow)，1m 收盤價站上 KC 中軌 (close > kc_middle)，
       EMA15 斜率大於 0 且無長上影線誘多時，validate_entry_math 必須回傳 allowed == True，
       放行原因為 ENTRY_LONG_EMA_CROSS_MID，不再強制等待突破 KC 上軌天花板！
    2. 空單 (SHORT)：EMA15 向下跌破 EMA40 (ma15 < ema_slow)，1m 收盤價跌破 KC 中軌 (close < kc_middle)，
       EMA15 斜率小於 0 且無長下影線誘空時，validate_entry_math 必須回傳 allowed == True，
       放行原因為 ENTRY_SHORT_EMA_CROSS_MID，不再強制等待跌破 KC 下軌地板！
    """
    import pandas as pd
    from core.services.math_execution_gate import validate_entry_math

    # 1. 多單驗證 (ENTRY_LONG_EMA_CROSS_MID)
    long_bars = [
        dict(timestamp=60_000, open=99.0, high=99.8, low=98.8, close=99.5,
             ma5=99.2, ma15=99.0, ema_slow=99.2, atr=2.0, kc_upper=104.0, kc_middle=99.0, kc_lower=95.0),
        dict(timestamp=120_000, open=99.5, high=100.8, low=99.4, close=100.5,  # close 100.5 > mid 99.2, upper 104.0
             ma5=99.8, ma15=99.6, ema_slow=99.3, atr=2.0, kc_upper=104.0, kc_middle=99.2, kc_lower=95.0),
    ]
    df_long = pd.DataFrame(long_bars)
    allowed_long, reason_long = validate_entry_math(df_long, side="LONG")
    assert allowed_long is True, f"多單在 EMA 金叉且站穩中軌時應允許提早開多！原因: {reason_long}"
    assert reason_long == "ENTRY_LONG_EMA_CROSS_MID"

    # 2. 空單驗證 (ENTRY_SHORT_EMA_CROSS_MID)
    short_bars = [
        dict(timestamp=60_000, open=101.0, high=101.2, low=100.2, close=100.5,
             ma5=100.8, ma15=101.0, ema_slow=100.8, atr=2.0, kc_upper=105.0, kc_middle=101.0, kc_lower=96.0),
        dict(timestamp=120_000, open=100.5, high=100.6, low=99.2, close=99.5,   # close 99.5 < mid 100.8, lower 96.0
             ma5=100.2, ma15=100.4, ema_slow=100.7, atr=2.0, kc_upper=105.0, kc_middle=100.8, kc_lower=96.0),
    ]
    df_short = pd.DataFrame(short_bars)
    allowed_short, reason_short = validate_entry_math(df_short, side="SHORT")
    assert allowed_short is True, f"空單在 EMA 死叉且跌破中軌時應允許提早開空！原因: {reason_short}"
    assert reason_short == "ENTRY_SHORT_EMA_CROSS_MID"


def test_entry_math_strictly_blocks_long_when_ema_is_falling():
    """
    [EMA 斜率下拐/走平嚴格一票否決開多單元測試]
    驗證：
    1. 構造一段上升趨勢後、末端兩根均線下拐 (ema15[-1] < ema15[-2]) 的行情：
       即便當時價格衝破 KC 上軌 (close > kc_upper 兩根破軌模式)，
       validate_entry_math(df, side="LONG") 必須 100% 回傳 allowed == False，
       原因必須為 BLOCKED_LONG_ON_EMA_FALLING，絕不准進場開多接飛刀！
    2. 均線走平 (ema15[-1] == ema15[-2]，斜率 <= 0) 同樣 100% 阻斷開多。
    """
    import pandas as pd
    from core.services.math_execution_gate import validate_entry_math

    # 1. 均線下拐情境 (含衝破 KC 上軌)
    breakout_falling_long_bars = [
        dict(timestamp=60_000, open=100.0, high=101.5, low=99.8, close=101.2,
             ma5=101.0, ma15=101.2, ema_slow=100.0, atr=1.5, kc_upper=104.0, kc_middle=100.5, kc_lower=97.0),
        # 第 2 根強勢衝破 KC 上軌 (close=104.5 > kc_upper=104.0)，但 ma15 下拐至 101.0 (101.0 < 101.2)
        dict(timestamp=120_000, open=101.2, high=104.6, low=101.0, close=104.5,
             ma5=102.8, ma15=101.0, ema_slow=100.1, atr=1.5, kc_upper=104.0, kc_middle=100.6, kc_lower=97.2),
    ]
    df_breakout = pd.DataFrame(breakout_falling_long_bars)
    allowed, reason = validate_entry_math(df_breakout, side="LONG")
    assert allowed is False, f"EMA15 下拐時哪怕兩根破軌也必須一票否決開多！實際: allowed={allowed}, reason={reason}"
    assert reason == "BLOCKED_LONG_ON_EMA_FALLING"

    # 2. 均線走平情境 (slope == 0)
    flat_long_bars = [
        dict(timestamp=60_000, open=100.0, high=101.5, low=99.8, close=101.2,
             ma5=101.0, ma15=101.2, ema_slow=100.0, atr=1.5, kc_upper=104.0, kc_middle=100.5, kc_lower=97.0),
        dict(timestamp=120_000, open=101.2, high=104.6, low=101.0, close=104.5,
             ma5=102.8, ma15=101.2, ema_slow=100.1, atr=1.5, kc_upper=104.0, kc_middle=100.6, kc_lower=97.2),
    ]
    df_flat = pd.DataFrame(flat_long_bars)
    allowed_flat, reason_flat = validate_entry_math(df_flat, side="LONG")
    assert allowed_flat is False, f"EMA15 走平 (slope <= 0) 時必須一票否決開多！實際: allowed={allowed_flat}, reason={reason_flat}"
    assert reason_flat == "BLOCKED_LONG_ON_EMA_FALLING"


def test_entry_math_strictly_blocks_short_when_ema_is_rising():
    """
    [EMA 斜率上翹/走平嚴格一票否決開空單元測試]
    驗證：
    1. 構造一段下跌趨勢後、末端兩根均線上揚 (ema15[-1] > ema15[-2]) 的行情：
       即便當時價格跌破 KC 下軌 (close < kc_lower 兩根破軌模式)，
       validate_entry_math(df, side="SHORT") 必須 100% 回傳 allowed == False，
       原因必須為 BLOCKED_SHORT_ON_EMA_RISING，絕不准進場開空摸頂！
    2. 均線走平 (ema15[-1] == ema15[-2]，斜率 >= 0) 同樣 100% 阻斷開空。
    """
    import pandas as pd
    from core.services.math_execution_gate import validate_entry_math

    # 1. 均線上揚情境 (含跌破 KC 下軌)
    breakdown_rising_short_bars = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=98.5, close=98.8,
             ma5=99.0, ma15=98.8, ema_slow=100.0, atr=1.5, kc_upper=103.0, kc_middle=99.5, kc_lower=96.0),
        # 第 2 根跌破 KC 下軌 (close=95.5 < kc_lower=96.0)，但 ma15 上揚至 99.1 (99.1 > 98.8)
        dict(timestamp=120_000, open=98.8, high=99.0, low=95.4, close=95.5,
             ma5=97.0, ma15=99.1, ema_slow=99.9, atr=1.5, kc_upper=102.8, kc_middle=99.4, kc_lower=96.0),
    ]
    df_breakdown = pd.DataFrame(breakdown_rising_short_bars)
    allowed, reason = validate_entry_math(df_breakdown, side="SHORT")
    assert allowed is False, f"EMA15 上揚時哪怕兩根破下軌也必須一票否決開空！實際: allowed={allowed}, reason={reason}"
    assert reason == "BLOCKED_SHORT_ON_EMA_RISING"

    # 2. 均線走平情境 (slope == 0)
    flat_short_bars = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=98.5, close=98.8,
             ma5=99.0, ma15=98.8, ema_slow=100.0, atr=1.5, kc_upper=103.0, kc_middle=99.5, kc_lower=96.0),
        dict(timestamp=120_000, open=98.8, high=99.0, low=95.4, close=95.5,
             ma5=97.0, ma15=98.8, ema_slow=99.9, atr=1.5, kc_upper=102.8, kc_middle=99.4, kc_lower=96.0),
    ]
    df_flat = pd.DataFrame(flat_short_bars)
    allowed_flat, reason_flat = validate_entry_math(df_flat, side="SHORT")
    assert allowed_flat is False, f"EMA15 走平 (slope >= 0) 時必須一票否決開空！實際: allowed={allowed_flat}, reason={reason_flat}"
    assert reason_flat == "BLOCKED_SHORT_ON_EMA_RISING"







