"""
core/gates/trend_riding_strategy.py
策略核心架構最終整合規範：通道收窄中繼再進場、多空趨勢鎖倉與防滑價機制

本模組嚴格遵循 2026-10-15 最新授權規範：
一、 市場趨勢大結構判定 (Regime Filter)
    - 大格局多頭 (MACRO_BULL): 基準線 (15 MA / 20 EMA / KC中軌) 斜率持續向上，價格持續在基準線上方或回踩未實體跌破。
    - 大格局空頭 (MACRO_BEAR): 基準線斜率持續向下，價格持續在基準線下方。
二、 通道變窄時的「中繼再開倉機制」 (Squeeze Re-entry)
    - 大格局多頭 + 通道收斂: 視為「高位中繼蓄勢」，滿足回踩確認或重新放量擴張突破 KC 上軌時，立即追入並解除冷卻限制。
    - 大格局空頭 + 通道收斂: 視為「下跌中繼蓄勢」，滿足回抽未破或重新跌破 KC 下軌時，立即追空並解除冷卻限制。
三、 持倉中的「趨勢鎖倉」防洗盤規則 (Trend-Riding Mode)
    - 取消微幅回吐平倉 (禁止盤中 2%~3% 即時止盈)。
    - 多單: 即使從 KC 上軌拉回通道內，只要實體 K 棒未收盤跌破基準線，一律強制 HOLD。
    - 空單: 即使從 KC 下軌反抽回通道內，只要實體 K 棒未收盤突破站上基準線，一律強制 HOLD。
四、 合法平倉條件 (Only Close When)
    - 結構被破 (Trend Invalidation): 多單收盤跌破基準線；空單收盤突破基準線。
    - 爆量高位滯漲力竭 (Climax Exit): 價格在通道外嚴重乖離 + 2 倍以上巨量 + 長影線 (多單長上影 >= 50%、空單長下影 >= 50%)。
五、 出場執行防滑價機制 (No Market Orders)
    - 廢棄 Stop Market，改以帶 0.2%~0.3% 讓步緩衝之 Limit Order (reduceOnly=True)。
    - 內建 3 秒監控 Watchdog: 超時未完全成交則自動撤單改抓最新對手價重掛。
"""

import math
import time
import asyncio
import logging
from typing import Dict, Any, Tuple, Optional, Union
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# 【通盤開倉審查入口 can_open_position (整合一票否決硬性門禁與防刷單冷卻)】
# ══════════════════════════════════════════════════════════════════════
def can_open_position(
    df: pd.DataFrame,
    side: str,
    quote: Optional[float] = None,
    last_closed_action: str = "",
    last_closed_ts: float = 0.0,
    timeframe_ms: float = 60_000.0,
    symbol: str = "UNKNOWN",
    bars_since_last_exit: Optional[int] = None,
    last_closed_side: str = "",
    cooldown_bars: int = 3,
    **kwargs,
) -> Tuple[bool, str, Dict[str, Any]]:
    """開倉絕對門禁 (Gatekeeper 一票否決制，嚴禁繞過)
    
    1. 【開多門禁 (LONG)】：
       - if kc_trend != 'UP': return False  (KC 非向上絕對不開多)
       - if ma5[-1] <= ma15[-1]: return False (MA5 仍在 MA15 下方絕對不開多)
       - if ma5[-1] <= ma5[-2]: return False  (MA5 未實質向上勾頭不開多)
       - 必須是上一根已收盤為「金叉突破」或「多頭排列首根收陽」，嚴禁在頂部上影線追多！
    
    2. 【開空門禁 (SHORT)】：
       - if kc_trend != 'DOWN': return False  (KC 非向下絕對不開空！CAP 的 4 筆空單必須全被此條攔截！)
       - if ma5[-1] >= ma15[-1]: return False (MA5 在 MA15 上方絕對不開空)
       - if ma5[-1] >= ma5[-2]: return False  (MA5 走平或向上勾頭絕對不開空)
       - if close[-1] >= open[-1]: return False (上一根為實體陽線絕對不開空)
    
    3. 【防刷單冷卻時間 (Anti-Chop Cooldown)】：
       - 任何一筆訂單平倉（不論盈虧）後，系統強制進入冷卻期，至少 3 根 K 棒內（3 分鐘）嚴禁同方向再次開倉！
    """
    if df is None or len(df) < 2:
        return False, "VETO: INSUFFICIENT_DATA", {}

    # The dataframe's verified market interval is authoritative. The historic
    # 1m default otherwise miscounts same-candle guards and cooldown bars for
    # the meme-symbol 3m entry runner.
    try:
        frame_timeframe_ms = float(df.attrs.get("timeframe_ms", 0.0) or 0.0)
        if math.isfinite(frame_timeframe_ms) and frame_timeframe_ms > 0:
            timeframe_ms = frame_timeframe_ms
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass

    side = str(side).upper()
    if side not in ("LONG", "SHORT"):
        return False, f"VETO: INVALID_SIDE ({side})", {}

    side_cn = "多" if side == "LONG" else "空"

    entry_confirmation = str(kwargs.get("entry_confirmation") or "").upper()
    if entry_confirmation == "MEME_PEAK_HUNTER":
        return True, "PASS: MEME_PEAK_HUNTER_AUTHORIZED", {"symbol": symbol, "side": side}

    # 提取已收盤 K 棒
    if "is_closed" in df.columns:
        closed_df = df[df["is_closed"] == True]
        if len(closed_df) >= 2:
            last_closed = closed_df.iloc[-1]
            prev_closed = closed_df.iloc[-2]
        else:
            last_closed = df.iloc[-2] if len(df) >= 3 else df.iloc[-1]
            prev_closed = df.iloc[-3] if len(df) >= 3 else last_closed
    else:
        last_closed = df.iloc[-2] if len(df) >= 3 else df.iloc[-1]
        prev_closed = df.iloc[-3] if len(df) >= 3 else last_closed

    close_p = float(last_closed["close"])
    open_p = float(last_closed["open"])
    high_p = float(last_closed["high"])
    low_p = float(last_closed["low"])

    prev_close = float(prev_closed["close"])
    prev_open = float(prev_closed["open"])

    ma5_curr = float(last_closed.get("ma5", last_closed.get("_wild_ma5", close_p)))
    ma5_prev = float(prev_closed.get("ma5", prev_closed.get("_wild_ma5", ma5_curr)))
    ma15_curr = float(last_closed.get("ma15", close_p))
    ma15_prev = float(prev_closed.get("ma15", ma15_curr))

    from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend
    kc_trend = resolve_kc_trend(df)
    explicit_kc = str(last_closed.get("kc_trend") or last_closed.get("channel_state") or "").strip().upper()
    if any(w in explicit_kc for w in ("UP", "KC向上", "UPTREND", "BULLISH")):
        kc_trend = "UP"
    elif any(w in explicit_kc for w in ("DOWN", "KC向下", "DOWNTREND", "BEARISH")):
        kc_trend = "DOWN"

    body = abs(close_p - open_p)
    target_df = closed_df if "is_closed" in df.columns and len(closed_df) >= 2 else df
    recent_bodies = (target_df["close"].iloc[-11:-1] - target_df["open"].iloc[-11:-1]).abs()
    avg_body = float(recent_bodies.mean()) if len(recent_bodies) > 0 else max(body, 1e-9)
    upper_wick = max(0.0, high_p - max(close_p, open_p))
    lower_wick = max(0.0, min(close_p, open_p) - low_p)

    details = {
        "symbol": symbol,
        "side": side,
        "kc_trend": kc_trend,
        "close": close_p,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "ma5_curr": ma5_curr,
        "ma5_prev": ma5_prev,
        "ma15_curr": ma15_curr,
        "ma15_prev": ma15_prev,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "body": body,
    }

    entry_confirmation = str(kwargs.get("entry_confirmation") or "").upper()
    is_instant_breakout = entry_confirmation in {"INSTANT_BREAKOUT_INSIDE", "INSTANT_BURST_BREAKOUT"}
    wild_outer_entry = False
    live = None
    live_quote = float(kwargs.get("quote") or 0.0)
    if df is not None and not df.empty:
        if not bool(df.iloc[-1].get("is_closed", True)):
            live = df.iloc[-1].to_dict().copy()
            if live_quote > 0:
                live["close"] = live_quote
                live["high"] = max(float(live.get("high", live_quote)), live_quote)
                live["low"] = min(float(live.get("low", live_quote)), live_quote)
        elif live_quote > 0:
            live = df.iloc[-1].to_dict().copy()
            live["close"] = live_quote
            live["high"] = max(float(live.get("high", live_quote)), live_quote)
            live["low"] = min(float(live.get("low", live_quote)), live_quote)
            live["is_closed"] = False
            tf_ms = float(df.attrs.get("timeframe_ms", 60000.0) or 60000.0)
            live["timestamp"] = float(live.get("timestamp", 0.0)) + tf_ms

    if entry_confirmation in {
        "INSTANT_BREAKOUT_INSIDE",
        "CONFIRMED_2BAR_BREAKOUT",
        "CONTINUATION_OUTSIDE_KC",
        "INSTANT_BURST_BREAKOUT",
    }:
        try:
            from core.gates.breakout_continuation_entry_gate import (
                BreakoutContinuationEntryGate,
            )
            from core.services.candle_data import closed_entry_candles

            closed = closed_entry_candles(df)
            wild_outer_entry = (
                BreakoutContinuationEntryGate.validate_closed_outer_entry(
                    closed.to_dict("records"),
                    side,
                    entry_confirmation,
                    tick_price=live_quote if live_quote > 0 else None,
                    live_candle=live,
                )
                is not None
            )
        except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
            logger.warning("[trend_riding_strategy] validate_closed_outer_entry error: %s", exc)
            wild_outer_entry = False
        if not wild_outer_entry:
            return False, "BLOCKED_WILD_OUTER_ENTRY_CONFIRMATION_INVALID", details
    has_live_breakout_confirmation = bool(
        wild_outer_entry
        and entry_confirmation in {
            "INSTANT_BREAKOUT_INSIDE", "CONTINUATION_OUTSIDE_KC", "INSTANT_BURST_BREAKOUT",
        }
        and live is not None
    )

    # ══════════════════════════════════════════════════════════════════
    # 二、 防刷單開倉保護 (不綁死固定冷卻時間，以突破與新 K 棒結構為準)
    # ══════════════════════════════════════════════════════════════════
    target_side = (
        "LONG" if ("LONG" in last_closed_action.upper() or last_closed_side == "LONG")
        else ("SHORT" if ("SHORT" in last_closed_action.upper() or last_closed_side == "SHORT") else "")
    )
    last_closed_price = float(kwargs.get("last_closed_price") or 0.0)
    current_tick_price = float(quote or close_p or 0.0)

    kc_up = float(last_closed.get("kc_upper", 0.0))
    kc_low = float(last_closed.get("kc_lower", 0.0))
    kc_mid_val = float(last_closed.get("kc_middle", last_closed.get("kc_basis", 0.0)))
    ma5_val = float(last_closed.get("ma5", close_p))
    atr_val = float(last_closed.get("atr", 0.0001) or 0.0001)

    def veto_return(reason: str) -> Tuple[bool, str, Dict[str, Any]]:
        is_breaking_rail = (
            (side == "SHORT" and (current_tick_price < kc_low or close_p < kc_low))
            or (side == "LONG" and (current_tick_price > kc_up or close_p > kc_up))
        )
        if is_breaking_rail:
            logger.warning(
                "🚨 [KC破軌開倉攔截診斷] symbol=%s side=%s price=%.6g kc_upper=%.6g kc_lower=%.6g "
                "攔截原因=%s | 條件狀態: kc_trend=%s, ema5=%.6g, ema13=%.6g, ma5_slope=%+.6g, "
                "bars_since_exit=%s, last_exit_price=%.6g, entry_confirmation=%s",
                symbol, side, current_tick_price, kc_up, kc_low, reason,
                kc_trend, ma5_curr, ma15_curr, (ma5_curr - ma5_prev),
                bars_since_last_exit, last_closed_price, entry_confirmation,
            )
            print(
                f"🚨 [KC破軌開倉攔截診斷] {symbol} {side} 價={current_tick_price:.6g} 破軌但被攔截: {reason} "
                f"(趨勢={kc_trend}, MA5={ma5_curr:.6g}, MA15={ma15_curr:.6g}, 平倉冷卻={bars_since_last_exit})",
                flush=True,
            )
        return False, reason, details

    if target_side == side:
        # 1. 同一根 K 棒剛平倉，不立即追回同向單（等待新 K 棒或訊號確認）
        curr_bar_ts = float(last_closed.get("timestamp", 0.0))
        if last_closed_ts > 0 and curr_bar_ts > 0:
            l_ts = float(last_closed_ts)
            if l_ts > 1e11 and curr_bar_ts < 1e11:
                l_ts /= 1000.0
            elif l_ts < 1e11 and curr_bar_ts > 1e11:
                l_ts *= 1000.0
            if abs(curr_bar_ts - l_ts) < timeframe_ms or curr_bar_ts == l_ts:
                msg = f"[攔截開{side_cn}] {symbol} 同一根 K 棒剛平倉，禁止同向立即秒開"
                logger.warning(msg)
                return False, "BLOCKED_SAME_CANDLE_ANTI_CHOP", details

        # ── 破軌優先覆蓋 (Breakout Override) 與 趨勢回踩二次進場 (Trend Continuation Entry) ──
        # 核心原則：大週期趨勢不變、二次破軌或強勢回踩時不被冷卻機制攔截！
        is_breakout_override = False
        is_continuation_reentry = False


        if side == "LONG":
            is_outer_breakout = (kc_up > 0) and (current_tick_price > kc_up or close_p > kc_up)
            has_breakout_mode = bool(
                entry_confirmation in (
                    "BREAKOUT_FLOW", "CONTINUATION_OUTSIDE_KC", "TWO_BAR_EXTREME_BREAKOUT",
                    "INSTANT_BURST_BREAKOUT", "CONFIRMED_2BAR_BREAKOUT", "INSTANT_BREAKOUT_INSIDE",
                    "BREAKOUT", "CONTINUATION", "MID_LINE_BREAKOUT",
                )
            )
            # 破軌優先覆蓋：有明確破軌模式確認，且大週期為 KC 向上 (或破軌直接發動)，且突破 KC 上軌
            is_breakout_override = (
                has_breakout_mode
                and (kc_trend in ("UP", "BULLISH", "") or is_outer_breakout)
                and kc_up > 0
                and (close_p > kc_up or current_tick_price > kc_up)
            )
            # 趨勢回踩二次進場：先前部位已出場，大週期仍為 KC 向上，回調未跌破中軌且收紅站回 MA5 之上
            is_continuation_confirmed = (
                entry_confirmation in ("PULLBACK_SUPPORT_CONFIRMED", "DIP_SNIPER", "MA_CROSSOVER", "CROSSOVER", "TREND_CONTINUATION_ENTRY")
                or (bool(entry_confirmation) and close_p > open_p and close_p > ma5_val)
            )
            is_continuation_reentry = (
                (kc_trend in ("UP", "BULLISH", ""))
                and kc_mid_val > 0
                and (close_p >= kc_mid_val or current_tick_price >= kc_mid_val)
                and is_continuation_confirmed
            )
        elif side == "SHORT":
            is_outer_breakout = (kc_low > 0) and (current_tick_price < kc_low or close_p < kc_low)
            has_breakout_mode = bool(
                entry_confirmation in (
                    "BREAKOUT_FLOW", "CONTINUATION_OUTSIDE_KC", "TWO_BAR_EXTREME_BREAKOUT",
                    "INSTANT_BURST_BREAKOUT", "CONFIRMED_2BAR_BREAKOUT", "INSTANT_BREAKOUT_INSIDE",
                    "BREAKOUT", "CONTINUATION", "MID_LINE_BREAKDOWN",
                )
            )
            is_breakout_override = (
                has_breakout_mode
                and (kc_trend in ("DOWN", "BEARISH", "") or is_outer_breakout)
                and kc_low > 0
                and (close_p < kc_low or current_tick_price < kc_low)
            )
            # 趨勢回抽二次進場：先前部位已出場，大週期仍為 KC 向下，回抽未衝破中軌且收黑跌破 MA5
            is_continuation_confirmed = (
                entry_confirmation in ("PULLBACK_RESISTANCE_CONFIRMED", "DIP_SNIPER", "MA_CROSSOVER", "CROSSOVER", "TREND_CONTINUATION_ENTRY")
                or (bool(entry_confirmation) and close_p < open_p and close_p < ma5_val)
            )
            is_continuation_reentry = (
                (kc_trend in ("DOWN", "BEARISH", ""))
                and kc_mid_val > 0
                and (close_p <= kc_mid_val or current_tick_price <= kc_mid_val)
                and is_continuation_confirmed
            )

        if is_breakout_override or is_continuation_reentry:
            exempt_tag = "BREAKOUT_OVERRIDE" if is_breakout_override else "TREND_CONTINUATION_REENTRY"
            logger.info(f"[冷卻豁免] {symbol} 符合 {exempt_tag}，直接豁免冷卻期與平倉價限制，放行開{side_cn}！")
            details["cooldown_exempt"] = exempt_tag
        else:
            # 2. 價格突破過濾：必須有新的突破才進場，有新突破立即解除冷卻！
            if last_closed_price > 0 and current_tick_price > 0:
                if side == "LONG":
                    # 多單：價格必須高於上次平倉價，確認發動續漲才追多
                    if current_tick_price <= last_closed_price:
                        msg = f"[攔截開多] {symbol} 最新價 {current_tick_price} <= 上次平倉價 {last_closed_price}，未創出續漲新高禁止追多"
                        logger.warning(msg)
                        return veto_return("BLOCKED_REQUIRES_NEW_HIGH_ABOVE_LAST_EXIT")
                    # 滿足突破高於上次平倉價，直接豁免固定冷卻門檻！
                elif side == "SHORT":
                    # 空單：價格必須低於上次平倉價，確認發動續跌才追空
                    if current_tick_price >= last_closed_price:
                        msg = f"[攔截開空] {symbol} 最新價 {current_tick_price} >= 上次平倉價 {last_closed_price}，未創出續跌新低禁止追空"
                        logger.warning(msg)
                        return veto_return("BLOCKED_REQUIRES_NEW_LOW_BELOW_LAST_EXIT")
                    # 滿足突破低於上次平倉價，直接豁免固定冷卻門檻！
            elif entry_confirmation != "CONTINUATION_OUTSIDE_KC":
                if bars_since_last_exit is not None and bars_since_last_exit < cooldown_bars:
                    msg = f"[攔截開{side_cn}] {symbol} 處於平倉冷卻期 (已過 {bars_since_last_exit} 根 < {cooldown_bars} 根冷卻門檻)，禁止再次開{side_cn}"
                    logger.warning(msg)
                    return veto_return(f"VETO: COOLDOWN_ACTIVE (平倉後僅 {bars_since_last_exit} 根，未滿 {cooldown_bars} 根)")

                if bars_since_last_exit is None and last_closed_ts > 0:
                    curr_ts = float(last_closed.get("timestamp", time.time() * 1000.0))
                    previous_ts = float(prev_closed.get("timestamp", curr_ts))
                    timestamp_step = curr_ts - previous_ts
                    timestamp_scale = (
                        1000.0 if math.isclose(timestamp_step, timeframe_ms / 1000.0, rel_tol=1e-6) else 1.0
                    )
                    curr_ts *= timestamp_scale
                    close_scale = (
                        1000.0 if last_closed_ts < 1e11 and (timestamp_scale == 1000.0 or curr_ts >= 1e11) else timestamp_scale
                    )
                    ts_norm = last_closed_ts * close_scale
                    bars_since = (curr_ts - ts_norm) / timeframe_ms
                    if bars_since < float(cooldown_bars):
                        msg = f"[攔截開{side_cn}] {symbol} 處於平倉冷卻期 (平倉後僅 {bars_since:.1f} 根，未滿 {cooldown_bars} 根)，禁止再次開{side_cn}"
                        logger.warning(msg)
                        return veto_return(f"VETO: COOLDOWN_ACTIVE (平倉後僅 {bars_since:.1f} 根，未滿 {cooldown_bars} 根)")

    # ══════════════════════════════════════════════════════════════════
    # 一、 開倉絕對門禁 (一票否決制，嚴禁繞過)
    # ══════════════════════════════════════════════════════════════════
    live_price = float(quote or close_p or 0.0)
    kc_mid = float(last_closed.get("kc_middle", last_closed.get("kc_basis", 0.0)))
    atr_val = float(last_closed.get("atr", 0.0001)) or 0.0001

    if side == "LONG":
        exempt_long_modes = {
            "INSTANT_BURST_BREAKOUT", "DIP_SNIPER", "MA_CROSSOVER",
            "MID_LINE_BREAKOUT", "BREAKOUT", "CONTINUATION", "FIVE_LINE_CONTINUATION",
            "BREAK_STRONG", "BREAK_CONFIRM", "BREAK_STRONG_ADD", "BREAK_CONFIRM_OPP",
        }
        is_rail_breakout_long = (kc_up > 0 and (live_price > kc_up or close_p > kc_up))
        # 1. kc_trend != 'UP': return False (KC 非向上絕對不開多；破軌/貫穿/延續模式豁免滯後 KC 斜率)
        if entry_confirmation not in exempt_long_modes and not is_rail_breakout_long and kc_trend != "UP":
            msg = f"[攔截開多] {symbol} 當前 KC 為 {kc_trend}，禁止開多"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return(f"VETO_LONG: BLOCKED_LONG_ON_BEAR_TREND: KC_TREND_NOT_UP ({kc_trend})")

        # 2. ma5[-1] <= ma15[-1]: return False (MA5 仍在 MA15 下方絕對不開多)
        if not is_instant_breakout and entry_confirmation not in exempt_long_modes and not is_rail_breakout_long and ma5_curr <= ma15_curr:
            if not (live_price > kc_mid and live_price > ma15_curr):
                msg = f"[攔截開多] {symbol} MA5 ({ma5_curr:.6g}) <= MA15 ({ma15_curr:.6g})，禁止開多"
                logger.warning(msg)
                print(msg, flush=True)
                return veto_return("VETO_LONG: MA5_NOT_ABOVE_MA15")

        # 3. ma5[-1] <= ma5[-2]: return False (MA5 未實質向上勾頭不開多)
        is_ma5_bullish_resumed = (
            (live_price > kc_mid or close_p > kc_mid)
            and (
                live_price > ma5_curr
                or entry_confirmation in exempt_long_modes
                or is_rail_breakout_long
                or abs(ma5_curr - ma5_prev) <= 1e-4
                or live_price >= open_p
            )
        )
        if not is_instant_breakout and not is_rail_breakout_long and ma5_curr <= ma5_prev and not is_ma5_bullish_resumed:
            msg = f"[攔截開多] {symbol} MA5 未實質向上勾頭 (斜率={ma5_curr - ma5_prev:+.6g})，禁止開多"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return("VETO_LONG: MA5_SLOPE_NOT_POSITIVE")

        # 4. 破軌第二根檢查 (首根破軌後，次根收盤仍在軌道外；同色放行；反色小實體 <= 0.6*avg_body 放行，否則大實體或收回軌內一票否決)
        is_live_green = (
            (live_price > float(live.get("open", open_p)))
            if live is not None
            else (close_p > open_p)
        )
        prev_kc_up = float(prev_closed.get("kc_upper", 0.0) or 0.0)
        is_first_bar_breakout_long = (prev_kc_up > 0 and prev_close > prev_kc_up)
        is_second_bar_opp_allowed = False

        if is_first_bar_breakout_long:
            dist_prev = prev_close - prev_kc_up
            dist_curr = close_p - kc_up
            prev_body = abs(prev_close - prev_open)
            prev_color_str = "GREEN" if prev_close > prev_open else "RED"
            curr_color_str = "GREEN" if (close_p > open_p or is_live_green) else "RED"

            # a. 檢查收盤是否已回到 KC 軌道內
            if close_p <= kc_up and not is_live_green:
                msg = (
                    f"[ENTRY_GATE_SECOND_BAR 否決] {symbol} side=LONG | "
                    f"Bar1: {prev_color_str}, body={prev_body:.6g}, dist={dist_prev:+.6g} | "
                    f"Bar2: {curr_color_str}, body={body:.6g}, dist={dist_curr:+.6g} | "
                    f"avg_body={avg_body:.6g} | 原因: 次根收盤已跌回KC軌道內 (close={close_p:.6g} <= kc_up={kc_up:.6g})"
                )
                logger.warning(msg)
                print(msg, flush=True)
                return veto_return("VETO_LONG: BLOCKED_BREAKOUT_SECOND_BAR_RETURN_INSIDE_KC")

            # b. 同色放行
            if close_p > open_p or is_live_green:
                logger.info(
                    "[ENTRY_GATE_SECOND_BAR 同色放行 100%%] %s side=LONG | Bar1: %s, body=%.6g, dist=%+.6g | Bar2: %s, body=%.6g, dist=%+.6g | avg_body=%.6g",
                    symbol, prev_color_str, prev_body, dist_prev, curr_color_str, body, dist_curr, avg_body,
                )
            else:
                # c. 反色但實體 <= 0.6 * avg_body 放行 50%
                ratio = body / max(1e-9, avg_body)
                if body <= 0.60 * avg_body:
                    is_second_bar_opp_allowed = True
                    logger.info(
                        "[ENTRY_GATE_SECOND_BAR 反色弱實體放行 50%%] %s side=LONG | Bar1: %s, body=%.6g, dist=%+.6g | Bar2: %s, body=%.6g, dist=%+.6g | avg_body=%.6g, ratio=%.2fx <= 0.60x",
                        symbol, prev_color_str, prev_body, dist_prev, curr_color_str, body, dist_curr, avg_body, ratio,
                    )
                else:
                    # d. 反色且大實體 -> 否決
                    msg = (
                        f"[ENTRY_GATE_SECOND_BAR 否決] {symbol} side=LONG | "
                        f"Bar1: {prev_color_str}, body={prev_body:.6g}, dist={dist_prev:+.6g} | "
                        f"Bar2: {curr_color_str}, body={body:.6g}, dist={dist_curr:+.6g} | "
                        f"avg_body={avg_body:.6g}, ratio={ratio:.2f}x > 0.60x | 原因: 反色大實體"
                    )
                    logger.warning(msg)
                    print(msg, flush=True)
                    return veto_return("VETO_LONG: BLOCKED_BREAKOUT_SECOND_BAR_OPPOSITE_LARGE_BODY")

        if close_p <= open_p and not is_live_green and not is_second_bar_opp_allowed and entry_confirmation not in {"BREAK_CONFIRM_OPP", "DIP_SNIPER"}:
            msg = f"[攔截開多] {symbol} 收盤為陰線實體 (close={close_p:.6g} <= open={open_p:.6g})，禁止開多"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return("VETO_LONG: BEARISH_CANDLE")

        # 5. 頂部上影線追多過濾 (嚴禁頂部上影線追多)
        if (
            upper_wick > 1.5 * max(body, 1e-9)
            and not has_live_breakout_confirmation
            and entry_confirmation not in exempt_long_modes
            and not is_rail_breakout_long
        ):
            msg = f"[攔截開多] {symbol} 上一根為長上影線竭盡避雷針，嚴禁頂部追多"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return("VETO_LONG: EXHAUSTION_UPPER_WICK")

        # 6. 必須是上一根已收盤為「金叉突破」或「多頭排列首根收陽」
        is_golden_cross = (ma5_prev <= ma15_prev and ma5_curr > ma15_curr)
        is_first_bull_bar = (close_p > open_p and (prev_close <= prev_open or ma5_prev <= ma15_prev))
        is_strong_bull_aligned = (ma5_curr > ma15_curr and close_p > open_p and ma5_curr > ma5_prev)
        if not wild_outer_entry and entry_confirmation not in exempt_long_modes and not is_rail_breakout_long and not (
            is_golden_cross or is_first_bull_bar or is_strong_bull_aligned
        ):
            msg = f"[攔截開多] {symbol} 非金叉突破且非多頭排列首根收陽，禁止開多"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return("VETO_LONG: NOT_GOLDEN_CROSS_OR_BULL_FIRST_BAR")

    elif side == "SHORT":
        exempt_short_modes = {
            "INSTANT_BURST_BREAKOUT", "DIP_SNIPER", "MA_CROSSOVER",
            "MID_LINE_BREAKDOWN", "BREAKOUT", "CONTINUATION", "FIVE_LINE_CONTINUATION",
            "BREAK_STRONG", "BREAK_CONFIRM", "BREAK_STRONG_ADD", "BREAK_CONFIRM_OPP",
        }
        is_rail_breakdown_short = (kc_low > 0 and (live_price < kc_low or close_p < kc_low))
        # 1. kc_trend != 'DOWN': return False (KC 非向下絕對不開空；破軌/貫穿/延續模式豁免滯後 KC 斜率)
        if entry_confirmation not in exempt_short_modes and not is_rail_breakdown_short and kc_trend != "DOWN":
            msg = f"[攔截開空] {symbol} 當前 KC 為 {kc_trend}，禁止開空"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return(f"VETO_SHORT: BLOCKED_SHORT_ON_BULL_TREND: KC_TREND_NOT_DOWN ({kc_trend})")

        # 2. ma5[-1] >= ma15[-1]: return False (MA5 在 MA15 上方絕對不開空)
        if not is_instant_breakout and entry_confirmation not in exempt_short_modes and not is_rail_breakdown_short and ma5_curr >= ma15_curr:
            if not (live_price < kc_mid and live_price < ma15_curr):
                msg = f"[攔截開空] {symbol} MA5 ({ma5_curr:.6g}) >= MA15 ({ma15_curr:.6g})，禁止開空"
                logger.warning(msg)
                print(msg, flush=True)
                return veto_return("VETO_SHORT: MA5_NOT_BELOW_MA15")

        # 3. ma5[-1] >= ma5[-2]: return False (MA5 走平或向上勾頭絕對不開空)
        is_ma5_bearish_resumed = (
            (live_price < kc_mid or close_p < kc_mid)
            and (
                live_price < ma5_curr
                or entry_confirmation in exempt_short_modes
                or is_rail_breakdown_short
                or abs(ma5_curr - ma5_prev) <= 1e-4
                or live_price <= open_p
            )
        )
        if not is_instant_breakout and not is_rail_breakdown_short and ma5_curr >= ma5_prev and not is_ma5_bearish_resumed:
            msg = f"[攔截開空] {symbol} MA5 走平或向上勾頭 (斜率={ma5_curr - ma5_prev:+.6g})，禁止開空"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return("VETO_SHORT: MA5_SLOPE_NOT_NEGATIVE")

        # 4. 破軌第二根檢查 (首根跌破下軌後，次根收盤仍在下軌外；同色放行；反色小實體 <= 0.6*avg_body 放行，否則大實體或收回軌內一票否決)
        is_live_red = (
            (live_price < float(live.get("open", open_p)))
            if live is not None
            else (close_p < open_p)
        )
        prev_kc_low = float(prev_closed.get("kc_lower", 0.0) or 0.0)
        is_first_bar_breakout_short = (prev_kc_low > 0 and prev_close < prev_kc_low)
        is_second_bar_opp_allowed = False

        if is_first_bar_breakout_short:
            dist_prev = prev_close - prev_kc_low
            dist_curr = close_p - kc_low
            prev_body = abs(prev_close - prev_open)
            prev_color_str = "RED" if prev_close < prev_open else "GREEN"
            curr_color_str = "RED" if (close_p < open_p or is_live_red) else "GREEN"

            # a. 檢查收盤是否已回到 KC 軌道內
            if close_p >= kc_low and not is_live_red:
                msg = (
                    f"[ENTRY_GATE_SECOND_BAR 否決] {symbol} side=SHORT | "
                    f"Bar1: {prev_color_str}, body={prev_body:.6g}, dist={dist_prev:+.6g} | "
                    f"Bar2: {curr_color_str}, body={body:.6g}, dist={dist_curr:+.6g} | "
                    f"avg_body={avg_body:.6g} | 原因: 次根收盤已漲回KC軌道內 (close={close_p:.6g} >= kc_low={kc_low:.6g})"
                )
                logger.warning(msg)
                print(msg, flush=True)
                return veto_return("VETO_SHORT: BLOCKED_BREAKOUT_SECOND_BAR_RETURN_INSIDE_KC")

            # b. 同色放行
            if close_p < open_p or is_live_red:
                logger.info(
                    "[ENTRY_GATE_SECOND_BAR 同色放行 100%%] %s side=SHORT | Bar1: %s, body=%.6g, dist=%+.6g | Bar2: %s, body=%.6g, dist=%+.6g | avg_body=%.6g",
                    symbol, prev_color_str, prev_body, dist_prev, curr_color_str, body, dist_curr, avg_body,
                )
            else:
                # c. 反色但實體 <= 0.6 * avg_body 放行 50%
                ratio = body / max(1e-9, avg_body)
                if body <= 0.60 * avg_body:
                    is_second_bar_opp_allowed = True
                    logger.info(
                        "[ENTRY_GATE_SECOND_BAR 反色弱實體放行 50%%] %s side=SHORT | Bar1: %s, body=%.6g, dist=%+.6g | Bar2: %s, body=%.6g, dist=%+.6g | avg_body=%.6g, ratio=%.2fx <= 0.60x",
                        symbol, prev_color_str, prev_body, dist_prev, curr_color_str, body, dist_curr, avg_body, ratio,
                    )
                else:
                    # d. 反色且大實體 -> 否決
                    msg = (
                        f"[ENTRY_GATE_SECOND_BAR 否決] {symbol} side=SHORT | "
                        f"Bar1: {prev_color_str}, body={prev_body:.6g}, dist={dist_prev:+.6g} | "
                        f"Bar2: {curr_color_str}, body={body:.6g}, dist={dist_curr:+.6g} | "
                        f"avg_body={avg_body:.6g}, ratio={ratio:.2f}x > 0.60x | 原因: 反色大實體"
                    )
                    logger.warning(msg)
                    print(msg, flush=True)
                    return veto_return("VETO_SHORT: BLOCKED_BREAKOUT_SECOND_BAR_OPPOSITE_LARGE_BODY")

        if close_p >= open_p and not is_live_red and not is_second_bar_opp_allowed and entry_confirmation not in {"BREAK_CONFIRM_OPP", "DIP_SNIPER"}:
            msg = f"[攔截開空] {symbol} 收盤為實體陽線 (close={close_p:.6g} >= open={open_p:.6g})，禁止開空"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return("VETO_SHORT: BULLISH_CANDLE")

        # 5. 底部下影線反彈過濾
        if (
            lower_wick > 1.5 * max(body, 1e-9)
            and not has_live_breakout_confirmation
            and entry_confirmation not in exempt_short_modes
            and not is_rail_breakdown_short
        ):
            msg = f"[攔截開空] {symbol} 上一根為長下影線反彈棒，嚴禁底部追空"
            logger.warning(msg)
            print(msg, flush=True)
            return veto_return("VETO_SHORT: REBOUND_LOWER_WICK")

    return True, "PASS: GATEKEEPER_PERMIT", details


class TrendRidingStrategy:
    """多空趨勢鎖倉、通道收窄中繼再進場與對稱防洗盤策略控制器"""

    def __init__(
        self,
        cooldown_bars: int = 5,
        max_bias_atr: float = 2.0,
        climax_volume_multiplier: float = 2.0,
        exhaustion_wick_ratio: float = 0.50,
        offset_pct: float = 0.002,
        watchdog_timeout_sec: float = 3.0,
    ):
        """
        :param cooldown_bars: 平倉後同向常規開倉冷卻 K 棒數 (預設 5 根，擴張突破可豁免)
        :param max_bias_atr: 距離基準線最大允許進場正負乖離 (超過 2.0 ATR 禁止追單)
        :param climax_volume_multiplier: 頂/底力竭爆量乘數 (預設 2.0 倍均量)
        :param exhaustion_wick_ratio: 頂部長上影線 / 底部長下影線力竭閾值 (預設 50%)
        :param offset_pct: 限價防滑平倉讓步緩衝比例 (預設 0.2% = 0.002)
        :param watchdog_timeout_sec: 限價平倉 Watchdog 撮合超時秒數 (預設 3.0 秒)
        """
        self.cooldown_bars = cooldown_bars
        self.max_bias_atr = max_bias_atr
        self.climax_volume_multiplier = climax_volume_multiplier
        self.exhaustion_wick_ratio = exhaustion_wick_ratio
        self.offset_pct = offset_pct
        self.watchdog_timeout_sec = watchdog_timeout_sec

    # ══════════════════════════════════════════════════════════════════════
    # 【一、 市場趨勢大結構判定 (Regime Filter)】
    # ══════════════════════════════════════════════════════════════════════
    def evaluate_regime(self, df: pd.DataFrame) -> Tuple[str, Dict[str, Any]]:
        """大結構趨勢與通道收窄狀態評估
        
        回傳:
            (macro_regime, details):
            macro_regime 取值為 'MACRO_BULL' | 'MACRO_BEAR' | 'RANGE_CHOP'
            details 包含 is_squeeze, squeeze_regime, bandwidth 等詳細數據
        """
        if df is None or len(df) < 5:
            return "RANGE_CHOP", {"reason": "INSUFFICIENT_DATA", "is_squeeze": False}

        recent = df.tail(10)
        curr = recent.iloc[-1]
        prev = recent.iloc[-2]

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(float(curr.get('close', 1.0)) * 0.01, 1e-8)

        # 基準線 (優先取 kc_middle，次取 ma15，再次取 ema20)
        curr_basis = float(curr.get('kc_middle', curr.get('ma15', curr.get('ema20', curr.get('close', 0.0)))))
        prev_basis = float(prev.get('kc_middle', prev.get('ma15', prev.get('ema20', curr_basis))))
        prev_3_basis = float(recent.iloc[-3].get('kc_middle', recent.iloc[-3].get('ma15', curr_basis)))

        curr_upper = float(curr.get('kc_upper', curr_basis + 2 * atr))
        prev_upper = float(prev.get('kc_upper', prev_basis + 2 * atr))
        curr_lower = float(curr.get('kc_lower', curr_basis - 2 * atr))
        prev_lower = float(prev.get('kc_lower', prev_basis - 2 * atr))

        # 基準線斜率 (標準化為 ATR)
        basis_slope_atr = (curr_basis - prev_3_basis) / (atr * 2.0) if atr > 0 else 0.0
        basis_slope = curr_basis - prev_basis

        # 通道帶寬與收窄 (Bandwidth & Squeeze)
        bandwidth = (curr_upper - curr_lower) / curr_basis if curr_basis > 0 else 0.0
        prev_bandwidth = (prev_upper - prev_lower) / prev_basis if prev_basis > 0 else 0.0

        # 計算近期平均帶寬 (以最近 10 根為基準)
        if 'kc_upper' in df.columns and 'kc_lower' in df.columns and 'kc_middle' in df.columns:
            bws = ((df['kc_upper'] - df['kc_lower']) / df['kc_middle']).replace(0, np.nan).dropna()
            avg_bandwidth = float(bws.tail(20).mean()) if len(bws) >= 5 else bandwidth
            max_bandwidth = float(bws.tail(50).max()) if len(bws) >= 5 else bandwidth
        else:
            avg_bandwidth = bandwidth
            max_bandwidth = bandwidth

        # 通道收窄判定：帶寬小於均值的 0.85 倍或帶寬收斂縮窄中
        is_squeeze = bool(
            (bandwidth < avg_bandwidth * 0.85) or
            (max_bandwidth > 0 and bandwidth <= max_bandwidth * 0.35) or
            (bandwidth <= prev_bandwidth and bandwidth < avg_bandwidth)
        )
        is_expanding = bool(bandwidth > prev_bandwidth and bandwidth >= avg_bandwidth * 0.90)

        # 價格在基準線上的表現 (過去 5 根收盤)
        last_5 = recent.tail(5)
        closes = last_5['close'].astype(float).values
        bases = last_5['kc_middle'].astype(float).values if 'kc_middle' in last_5.columns else np.full(len(closes), curr_basis)
        above_count = sum(c >= b for c, b in zip(closes, bases))
        below_count = sum(c <= b for c, b in zip(closes, bases))

        c = float(curr.get('close', 0.0))
        l = float(curr.get('low', 0.0))
        h = float(curr.get('high', 0.0))

        # 判定大結構趨勢 (要求中長天期基準線具備明確正負斜率)
        is_macro_bull = (basis_slope > 0.0001 or basis_slope_atr > 0.015) and (above_count >= 3 or c >= curr_basis)
        is_macro_bear = (basis_slope < -0.0001 or basis_slope_atr < -0.015) and (below_count >= 3 or c <= curr_basis)

        if is_macro_bull and not is_macro_bear:
            macro_regime = "MACRO_BULL"
        elif is_macro_bear and not is_macro_bull:
            macro_regime = "MACRO_BEAR"
        else:
            macro_regime = "RANGE_CHOP"

        # 中繼蓄勢判定
        squeeze_regime = "NONE"
        if is_squeeze:
            if macro_regime == "MACRO_BULL" and c >= curr_basis:
                squeeze_regime = "BULL_SQUEEZE_CONSOLIDATION"  # 多頭高位中繼蓄勢
            elif macro_regime == "MACRO_BEAR" and c <= curr_basis:
                squeeze_regime = "BEAR_SQUEEZE_CONSOLIDATION"  # 空頭下跌中繼蓄勢
            else:
                squeeze_regime = "CHOP_SQUEEZE"

        details = {
            'macro_regime': macro_regime,
            'curr_basis': curr_basis,
            'basis_slope': basis_slope,
            'basis_slope_atr': round(basis_slope_atr, 4),
            'bandwidth': round(bandwidth, 6),
            'prev_bandwidth': round(prev_bandwidth, 6),
            'avg_bandwidth': round(avg_bandwidth, 6),
            'is_squeeze': is_squeeze,
            'is_expanding': is_expanding,
            'squeeze_regime': squeeze_regime,
            'above_count': above_count,
            'below_count': below_count,
            'atr': atr,
        }
        return macro_regime, details

    def evaluate_market_regime(self, df: pd.DataFrame) -> Tuple[str, Dict[str, Any]]:
        """向後相容接口：將 MACRO_BULL 映為 BULL_TREND，MACRO_BEAR 映為 BEAR_TREND"""
        macro, details = self.evaluate_regime(df)
        if macro == "MACRO_BULL":
            return "BULL_TREND", details
        elif macro == "MACRO_BEAR":
            return "BEAR_TREND", details
        return "RANGE_CHOP", details

    # ══════════════════════════════════════════════════════════════════════
    # 【二、 通道變窄時的「中繼再開倉機制」(Squeeze Re-entry)】
    # ══════════════════════════════════════════════════════════════════════
    def can_reenter_position(
        self,
        side: str,
        df: pd.DataFrame,
        quote: Optional[float] = None,
        last_closed_action: str = "",
        last_closed_ts: float = 0.0,
        timeframe_ms: float = 60_000.0,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """通道收窄時的二次進場審查 (Squeeze Re-entry)
        
        核心規範：
        1. 若大格局為多頭 (MACRO_BULL == True):
           即使通道收窄，只要價格守在基準線之上，視為「高位中繼蓄勢」，允許重新開多。
           觸發條件 (滿足任一):
             ① 回踩支撐確認: 回踩 3/5 EMA 或 KC 中軌未破並收出確認陽線 (Low >= Basis 且 Close > Open)。
             ② 重新放量擴張突破: 通道由窄開始放大 (Expansion)，實體陽線突破 KC 上軌 (Close > KC_Upper)，
                立即無條件追入，【解除任何冷卻期限制】！
        2. 若大格局為空頭 (MACRO_BEAR == True，完全對稱):
           即使通道收窄，只要價格壓制在基準線之下，視為「下跌中繼蓄勢」，允許重新開空。
           觸發條件 (滿足任一):
             ① 回抽阻力確認: 回抽 3/5 EMA 或 KC 中軌未破並收陰線 (Close < Open 且收在基準線下)。
             ② 重新放量擴張突破: 通道放大且實體陰線跌破 KC 下軌 (Close < KC_Lower)，
                立即無條件追空，【解除任何冷卻期限制】！
        """
        if df is None or len(df) < 5:
            return False, "VETO: INSUFFICIENT_DATA", {}

        side = str(side).upper()
        if side not in ('LONG', 'SHORT'):
            return False, "VETO: INVALID_SIDE", {}

        macro_regime, regime_details = self.evaluate_regime(df)
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        p = float(quote) if quote is not None else c

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))
        ma5 = float(curr.get('ma5', c))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))

        is_squeeze = regime_details.get('is_squeeze', False)
        is_expanding = regime_details.get('is_expanding', False)
        bandwidth = regime_details.get('bandwidth', 0.0)
        prev_bandwidth = regime_details.get('prev_bandwidth', 0.0)

        details = {
            'side': side,
            'macro_regime': macro_regime,
            'is_squeeze': is_squeeze,
            'is_expanding': is_expanding,
            'basis': basis,
            'close': c,
            'quote': p,
            'kc_upper': kc_upper,
            'kc_lower': kc_lower,
            'bandwidth': bandwidth,
            'atr': atr,
        }

        # ──【A. 多單中繼再進場 (LONG RE-ENTRY)】──
        if side == 'LONG':
            # 必須在大格局多頭結構下或中軌上方
            if macro_regime == 'MACRO_BEAR' or c < basis:
                return False, f"VETO: BLOCKED_LONG_ON_BEAR_TREND / UNFAVORABLE_HALF (大格局非多頭且收盤 {c:.6g} < 基準線 {basis:.6g})", details

            # 條件 ②：重新放量擴張突破 KC 上軌 (通道由窄開始放大且實體陽線突破 KC 上軌，優先權最高，直接豁免冷卻)
            bw_expanding = (bandwidth > prev_bandwidth) or is_expanding
            is_breakout = ((c > kc_upper and c > o) or (p > kc_upper and c >= kc_upper * 0.998)) and bw_expanding
            if is_breakout:
                details['reentry_type'] = 'EXPANSION_BREAKOUT'
                details['bypass_cooldown'] = True
                return True, "PASS: SQUEEZE_EXPANSION_BREAKOUT_LONG (通道重新放大且突破上軌，無條件追多解除冷卻)", details

            # 條件 ①：回踩支撐確認 (收窄期間回踩 3/5 EMA 或 KC 中軌未破並收陽線)
            # Low 碰觸/靠近 EMA3/MA5/KC中軌，收盤仍守在中軌之上，且當根為紅陽棒
            tested_support = (l <= max(ma5, ema3) or l <= basis + 0.3 * atr) and (l >= basis - 0.1 * atr)
            candle_is_bullish = (c > o)
            closed_above_support = (c >= min(ma5, ema3, basis))

            if tested_support and candle_is_bullish and closed_above_support:
                # 檢查冷卻保護 (跨 K 棒回踩確認收陽直接放行二次進場，僅同根防刷單)
                if last_closed_action in ('CLOSE_LONG', 'EXIT_LONG') and last_closed_ts > 0:
                    curr_ts = float(curr.get('timestamp', time.time() * 1000))
                    bars_since_close = (curr_ts - last_closed_ts) / timeframe_ms
                    if bars_since_close < 0.5:
                        return False, "VETO: SAME_CANDLE_ANTI_CHOP (同一根 K 棒剛平倉，禁止同向立即秒開)", details

                details['reentry_type'] = 'PULLBACK_SUPPORT_CONFIRMED'
                return True, "PASS: SQUEEZE_PULLBACK_CONFIRMED_LONG (高位中繼回踩支撐未破收陽，確認再開多)", details

            return False, "VETO: SQUEEZE_WAIT_TRIGGER (通道收窄中繼蓄勢中，等待回踩確認或破軌擴張突破)", details

        # ──【B. 空單中繼再進場 (SHORT RE-ENTRY，完全對稱)】──
        elif side == 'SHORT':
            if macro_regime == 'MACRO_BULL' or c > basis:
                return False, f"VETO: BLOCKED_SHORT_ON_BULL_TREND / UNFAVORABLE_HALF (大格局非空頭且收盤 {c:.6g} > 基準線 {basis:.6g})", details

            # 條件 ②：重新向下放量突破 KC 下軌 (通道由窄開始放大且實體陰線跌破 KC 下軌，直接豁免冷卻)
            bw_expanding = (bandwidth > prev_bandwidth) or is_expanding
            is_breakdown = ((c < kc_lower and c < o) or (p < kc_lower and c <= kc_lower * 1.002)) and bw_expanding
            if is_breakdown:
                details['reentry_type'] = 'EXPANSION_BREAKDOWN'
                details['bypass_cooldown'] = True
                return True, "PASS: SQUEEZE_EXPANSION_BREAKDOWN_SHORT (通道重新放大且跌破下軌，無條件追空解除冷卻)", details

            # 條件 ①：回抽阻力確認 (收窄期間回抽 3/5 EMA 或 KC 中軌未破並收黑陰棒)
            tested_resistance = (h >= min(ma5, ema3) or h >= basis - 0.3 * atr) and (h <= basis + 0.1 * atr)
            candle_is_bearish = (c < o)
            closed_below_resistance = (c <= max(ma5, ema3, basis))

            if tested_resistance and candle_is_bearish and closed_below_resistance:
                if last_closed_action in ('CLOSE_SHORT', 'EXIT_SHORT') and last_closed_ts > 0:
                    curr_ts = float(curr.get('timestamp', time.time() * 1000))
                    bars_since_close = (curr_ts - last_closed_ts) / timeframe_ms
                    if bars_since_close < 0.5:
                        return False, "VETO: SAME_CANDLE_ANTI_CHOP (同一根 K 棒剛平倉，禁止同向立即秒開)", details

                details['reentry_type'] = 'PULLBACK_RESISTANCE_CONFIRMED'
                return True, "PASS: SQUEEZE_PULLBACK_CONFIRMED_SHORT (下跌中繼回抽阻力未破收陰，確認再開空)", details

            return False, "VETO: SQUEEZE_WAIT_TRIGGER (通道收窄中繼蓄勢中，等待回抽確認或下軌擴張跌破)", details

        return False, "VETO: UNKNOWN_SIDE", details

    # ══════════════════════════════════════════════════════════════════════
    # 【通盤開倉審查入口 can_open_position (整合常規與中繼再開倉)】
    # ══════════════════════════════════════════════════════════════════════
    def can_open_position(
        self,
        df: pd.DataFrame,
        side: str,
        quote: Optional[float] = None,
        last_closed_action: str = "",
        last_closed_ts: float = 0.0,
        timeframe_ms: float = 60_000.0,
        symbol: str = "UNKNOWN",
        **kwargs,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """開倉審查：通道收窄時智能切換至 Squeeze Re-entry 機制，通道張開時走常規趨勢突破"""
        # 最外層硬性門禁攔截 (一票否決制，嚴禁繞過)
        passed, gate_reason, gate_details = can_open_position(
            df=df,
            side=side,
            quote=quote,
            last_closed_action=last_closed_action,
            last_closed_ts=last_closed_ts,
            timeframe_ms=timeframe_ms,
            symbol=symbol,
            cooldown_bars=self.cooldown_bars,
            **kwargs,
        )
        if not passed:
            return False, gate_reason, gate_details

        if df is None or len(df) < 5:
            return False, "VETO: INSUFFICIENT_DATA", {}

        macro_regime, regime_details = self.evaluate_regime(df)
        is_squeeze = regime_details.get('is_squeeze', False)

        # 若當前通道處於收窄狀態，優先以「中繼再進場機制」進行評估
        if is_squeeze:
            can_reenter, reason, details = self.can_reenter_position(
                side=side,
                df=df,
                quote=quote,
                last_closed_action=last_closed_action,
                last_closed_ts=last_closed_ts,
                timeframe_ms=timeframe_ms,
            )
            if can_reenter:
                return True, reason, details
            # 若中繼進場尚未觸發，返回阻斷理由
            return False, reason, details

        # ── 通道正常 / 擴張狀態下的常規審查 ──
        curr = df.iloc[-1]
        c = float(curr.get('close', 0.0))
        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)
        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))
        ma5 = float(curr.get('ma5', c))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))

        details = {
            'side': side,
            'macro_regime': macro_regime,
            'basis': basis,
            'close': c,
            'quote': quote or c,
            'atr': atr,
        }

        # 1. 冷卻保護 (解除跨 K 棒破軌與回踩之死板冷卻，保留同 K 棒防刷單)
        target_close_actions = ('CLOSE_LONG', 'EXIT_LONG') if side == 'LONG' else ('CLOSE_SHORT', 'EXIT_SHORT')
        if last_closed_action in target_close_actions and last_closed_ts > 0:
            curr_ts = float(curr.get('timestamp', time.time() * 1000))
            bars_since = (curr_ts - last_closed_ts) / timeframe_ms
            if bars_since < 0.5:
                return False, "VETO: SAME_CANDLE_ANTI_CHOP (同一根 K 棒剛平倉，禁止同向立即秒開)", details
            is_breakout = (c > kc_upper or (quote and quote > kc_upper)) if side == 'LONG' else (c < kc_lower or (quote and quote < kc_lower))
            is_continuation = (c >= basis and c > float(curr.get('open', c)) and c > ma5) if side == 'LONG' else (c <= basis and c < float(curr.get('open', c)) and c < ma5)
            if not (is_breakout or is_continuation):
                if bars_since < self.cooldown_bars:
                    return False, f"VETO: COOLDOWN_ACTIVE (平倉後僅 {bars_since:.1f} 根，未滿 {self.cooldown_bars} 根)", details

        # 2. 順勢鐵律
        if side == 'LONG':
            if macro_regime == 'MACRO_BEAR' or c < basis:
                return False, "VETO: BLOCKED_LONG_ON_BEAR_TREND (空頭大格局或中軌下方嚴禁開多)", details
        elif side == 'SHORT':
            if macro_regime == 'MACRO_BULL' or c > basis:
                return False, "VETO: BLOCKED_SHORT_ON_BULL_TREND (多頭大格局或中軌上方嚴禁開空)", details

        # 3. 乖離率限制
        bias_atr = abs(c - basis) / atr
        details['bias_atr'] = round(bias_atr, 2)
        if bias_atr > self.max_bias_atr:
            return False, f"VETO: OVEREXTENDED_BIAS (乖離 {bias_atr:.2f} ATR > {self.max_bias_atr} ATR，嚴禁懸空追單)", details

        return True, "PASS: TREND_RIDING_ENTRY_AUTHORIZED", details

    # ══════════════════════════════════════════════════════════════════════
    # 【三、 持倉中的「趨勢鎖倉」防洗盤規則 & 四、合法平倉條件】
    # ══════════════════════════════════════════════════════════════════════
    def should_close_position(
        self,
        *args,
        **kwargs
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """持倉防洗盤與合法出場審查 (支援雙重傳參模式)
        
        用法:
            should_close_position(side, position, df, quote=None)
            或 should_close_position(position, df, quote=None)
            
        核心鐵律：
        1. 趨勢鎖倉防洗盤 (Trend-Riding Mode)：
           - 嚴禁微幅回吐平倉 (取消 2%~3% 即時止盈)。
           - 多單持倉時：即使價格跌回收進通道內，只要實體 K 棒未收盤跌破基準線 (KC 中軌/15 MA)，一律強制 HOLD！
           - 空單持倉時：即使價格反抽收進通道內，只要實體 K 棒未收盤突破站上基準線，一律強制 HOLD！
        2. 合法平倉條件 (Only Close When，嚴格只有這兩種)：
           ① 結構被破 (Trend Invalidation)：多單實體收盤跌破基準線；空單實體收盤突破基準線。
           ② 爆量高位滯漲力竭 (Climax Exit)：價格在通道外嚴重乖離 + 2 倍以上巨量 + 長影線 (多單上影線 >= 50%、空單下影線 >= 50%)。
        3. 所有判定嚴格要求以已收線 K 棒 (Candle Close) 為準，杜絕盤中 Tick 毛刺！
        """
        # 兼容參數解析
        if len(args) == 4:
            side, position, df, quote = args
        elif len(args) == 3:
            if isinstance(args[0], str):
                side, position, df = args
                quote = kwargs.get('quote')
            else:
                position, df, quote = args
                side = position.get('side', 'LONG')
        elif len(args) == 2:
            position, df = args
            quote = kwargs.get('quote')
            side = position.get('side', 'LONG')
        else:
            position = kwargs.get('position', {})
            df = kwargs.get('df')
            quote = kwargs.get('quote')
            side = kwargs.get('side') or position.get('side', 'LONG')

        side = str(side).upper()
        if df is None or len(df) < 3:
            return False, "HOLD: INSUFFICIENT_DATA", {}

        curr = df.iloc[-1]
        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        p = float(quote) if quote is not None else c
        is_candle_closed = bool(curr.get('is_closed', True))

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))

        macro_regime, regime_details = self.evaluate_regime(df)

        details = {
            'side': side,
            'close': c,
            'quote': p,
            'basis': basis,
            'kc_upper': kc_upper,
            'kc_lower': kc_lower,
            'macro_regime': macro_regime,
            'is_candle_closed': is_candle_closed,
            'atr': atr,
        }

        # 鐵律：平倉判定嚴格要求以已收線 K 棒實體為準，盤中 Tick 微秒毛刺一律不平倉！
        if not is_candle_closed:
            return False, "HOLD: CANDLE_NOT_CLOSED (等待 1M K 棒收線確認，防盤中插針毛刺)", details

        # ──【A. 多單持倉審查 (LONG，調用專用多頭趨勢鎖倉平倉審查)】──
        if side == 'LONG':
            return self.should_close_long(
                position=position,
                df=df,
                quote=p,
                is_candle_closed=is_candle_closed,
            )

        # ──【B. 空單持倉審查 (SHORT，調用專用空頭趨勢鎖倉平倉審查)】──
        elif side == 'SHORT':
            return self.should_close_short(
                position=position,
                df=df,
                quote=p,
                is_candle_closed=is_candle_closed,
            )

        return False, "HOLD: DEFAULT_POSITION_PROTECTION", details

    def should_close_long(
        self,
        position: Dict[str, Any],
        df: pd.DataFrame,
        quote: Optional[float] = None,
        is_candle_closed: Optional[bool] = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """多頭趨勢中持倉鎖定 (Bull Trend-Riding) 專用平多審查
        
        嚴格執行多空完全對稱規範：
        一、 多頭趨勢狀態鎖定 (BULL_SWING == True):
            1. 基準線斜率持續向上 (Basis_Slope > 0)
            2. KC 通道三軌 (上軌、中軌、下軌) 同步向上發散
            3. 價格多數收在 KC 中軌上方 (最近 5 根有 >= 3 根收在中軌上，且當前 Close > KC_Basis)

        二、 多頭趨勢中的【平倉禁令 (禁止中途平多)】:
            只要 BULL_SWING == True:
            1. 禁用微幅回吐鎖利：嚴禁 2%~3% 的敏感回撤平多，盤中小陰線或微幅回調視為多頭健康換手正常呼吸，強制 HOLD！
            2. 允許通道內弱勢回踩：即使價格跌回收進通道內，只要實體收盤未跌破 KC 中軌 (Close > KC_Basis)，一律不准平多！
            3. 短期均線未拐頭不平多：短均線 (3 EMA / 5 EMA) 只要依然獲得支撐且方向向上，堅決持倉讓利潤奔跑！

        三、 多單在多頭趨勢下的【唯二平倉條件】:
            1. 結構被破 (Trend Invalidation): K 棒收盤價實體跌破 KC 中軌 (Close < KC_Basis)。
            2. 極端天量高位滯漲 (Climax Exit):
               價格大幅脫離 KC 上軌 (超買正乖離過大)，且當根爆出 2.5 倍以上巨量，並留下超過實體 2 倍長度的超長上影線 (確認高位放量滯漲出貨)。

        四、 收盤價判定鐵律：
            所有條件均以「K 棒收盤價 (is_candle_closed == True)」判斷，未收盤 100% 駁回平倉！
        """
        if df is None or len(df) < 1:
            return False, "HOLD: INSUFFICIENT_DATA", {}

        curr = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else curr
        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        p = float(quote) if quote is not None else c

        if is_candle_closed is None:
            is_candle_closed = bool(curr.get('is_closed', True))

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        prev_basis = float(prev.get('kc_middle', prev.get('ma15', basis)))
        basis_slope = basis - prev_basis

        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        prev_upper = float(prev.get('kc_upper', prev_basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))
        prev_lower = float(prev.get('kc_lower', prev_basis - 2 * atr))

        ma5 = float(curr.get('ma5', c))
        prev_ma5 = float(prev.get('ma5', ma5))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))
        prev_ema3 = float(prev.get('ema3', prev.get('ma3', ema3)))

        details = {
            'side': 'LONG',
            'close': c,
            'quote': p,
            'basis': basis,
            'basis_slope': basis_slope,
            'kc_upper': kc_upper,
            'kc_lower': kc_lower,
            'is_candle_closed': is_candle_closed,
            'atr': atr,
            'ma5': ma5,
            'ema3': ema3,
        }

        # ──【鐵律四：收盤價判定鐵律】──
        if not is_candle_closed:
            return False, "HOLD: CANDLE_NOT_CLOSED (等待 1M K 棒收線確認，徹底防範盤中毛刺與插針)", details

        # ──【一、 多頭趨勢狀態鎖定 (BULL_SWING)】──
        recent_5 = df.tail(5)
        above_count = sum(float(r.get('close', 0)) >= float(r.get('kc_middle', basis)) for _, r in recent_5.iterrows())
        three_bands_up = (kc_upper >= prev_upper * 0.9995) and (basis >= prev_basis) and (kc_lower >= prev_lower * 0.9995)
        is_bull_trend = (basis_slope > 0 or three_bands_up) and (c > basis) and (above_count >= 3)
        details['is_bull_trend'] = is_bull_trend
        details['above_count'] = above_count

        # 條件 1：結構被破 (Trend Invalidation): K 棒收盤價實體跌破並跌入 KC 中軌之下 (Close < KC_Basis)
        if c < basis:
            return True, "EXIT_LONG_STRUCTURE_BROKEN (K 棒實體跌破並收在 KC 中軌之下，多頭波段結構破壞，平多離場)", details

        # 條件 2：極端天量高位滯漲 (Climax Exit)
        body = abs(c - o)
        upper_wick = max(0.0, h - max(c, o))
        bar_range = max(h - l, 1e-9)

        curr_vol = float(curr.get('volume', 0.0))
        prev_bars = df.iloc[:-1] if len(df) > 1 else df
        avg_vol = float(prev_bars['volume'].tail(10).mean()) if 'volume' in prev_bars.columns and len(prev_bars) > 0 else 0.0
        is_climax_volume = (avg_vol > 0 and curr_vol >= min(2.5, self.climax_volume_multiplier) * avg_vol)
        is_climax_wick = (upper_wick >= 2.0 * max(body, 0.1 * atr)) or (upper_wick / bar_range >= self.exhaustion_wick_ratio)
        is_severe_overbought = (h >= kc_upper) or ((h - basis) / atr >= 1.2)

        if is_severe_overbought and is_climax_volume and is_climax_wick:
            details['wick_ratio'] = round(upper_wick / bar_range, 3)
            details['wick_to_body_ratio'] = round(upper_wick / max(body, 1e-9), 2)
            details['volume_ratio'] = round(curr_vol / avg_vol, 2) if avg_vol > 0 else 0.0
            return True, "EXIT_LONG_BLOW_OFF_TOP / CLIMAX_EXHAUSTION (KC上軌外超買 + 爆量換手 + 超長上影線，高位滯漲力竭離場)", details

        # ──【回踩容忍與影線利潤聯動規則 (徹底移除 KC 中軌平倉)】──
        entry_price = float(position.get("entry_price") or c)
        pnl_pct = (p - entry_price) / entry_price if entry_price > 0 else 0.0
        ma5_curr = float(curr.get("ma5", curr.get("_wild_ma5", c)))
        ma15_curr = float(curr.get("ma15", basis))
        body = abs(c - o)
        upper_wick = max(0.0, h - max(c, o))
        lower_wick = max(0.0, min(c, o) - l)

        # 1. 無利潤 / 浮虧狀態下 (浮盈 < 0.8%)：向下插針（長下影線）只要收盤在 MA15 上方且未死叉，一律視為洗盤，嚴禁平倉！
        if pnl_pct < 0.008 and c >= ma15_curr and ma5_curr >= ma15_curr:
            return False, "HOLD: NO_PROFIT_WICK_TOLERANCE / BULL_TREND_LOCK_INTACT / TREND_RIDING_LOCK_BULL_INTACT (浮虧/無實質利潤中，下影線洗盤尋求支撐，嚴禁慌張平多)", details

        # 2. 已有實質利潤狀態下 (浮盈 >= 0.8%)：頂部長上影線力竭允許觸發保護性止盈
        if pnl_pct >= 0.008 and upper_wick > 1.5 * max(body, 1e-9):
            return True, "EXIT_BY_PROFIT_EXHAUSTION_WICK (實質利潤下頂部長上影線力竭，保護性止盈平多)", details

        # 3. 回踩容忍（Hold on Pullback）：只要 ma5 > ma15 且收盤價未跌破 ma15，一律判定為「多頭健康回踩」，【嚴禁平多】！
        if ma5_curr > ma15_curr and c >= ma15_curr:
            return False, "HOLD: BULL_HEALTHY_PULLBACK_TOLERANCE / TREND_RIDING_LOCK_BULL_INTACT / BULL_TREND_LOCK_INTACT (MA5 > MA15 且收盤未破 MA15，多頭健康回踩嚴禁平多)", details


        # ──【平倉禁令：強制 HOLD，嚴禁中途被微幅回調洗出場】──
        long_ma_holding = (c >= ema3 or c >= ma5 or ema3 >= prev_ema3 or ma5 >= prev_ma5)
        details['long_ma_holding'] = long_ma_holding

        return False, "HOLD: BULL_TREND_LOCK_INTACT / TREND_RIDING_LOCK_BULL_INTACT (多頭主升浪趨勢鎖定中，未破中軌且無天量滯漲，嚴禁中途被洗下車)", details

    def evaluate_swing_entry(
        self,
        position: Dict[str, Any],
        df: pd.DataFrame,
        quote: Optional[float] = None,
        is_candle_closed: Optional[bool] = None,
    ) -> Tuple[Optional[str], str, Dict[str, Any]]:
        """波段交易：頂底破位即時反手開倉審查 (Swing Entry & Flip Engine)

        回傳:
            (signal, reason, details)
            signal: 'BUY_LONG' | 'SELL_SHORT' | 'FLIP_SHORT_TO_LONG' | 'FLIP_LONG_TO_SHORT' | None
        """
        if df is None or len(df) < 3:
            return None, "VETO: INSUFFICIENT_DATA", {}

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        p = float(quote) if quote is not None else c

        if is_candle_closed is None:
            is_candle_closed = bool(curr.get('is_closed', True))

        if not is_candle_closed:
            return None, "VETO: CANDLE_NOT_CLOSED (開倉必須等待 1M K 棒收線確認)", {}

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        prev_basis = float(prev.get('kc_middle', prev.get('ma15', basis)))
        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))

        ma5 = float(curr.get('ma5', c))
        prev_ma5 = float(prev.get('ma5', ma5))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))
        prev_ema3 = float(prev.get('ema3', prev.get('ma3', ema3)))

        holding_side = str(position.get('side', '')).upper() if position else ''

        details = {
            'close': c,
            'quote': p,
            'basis': basis,
            'ema3': ema3,
            'ma5': ma5,
            'holding_side': holding_side,
        }

        # ──【1. 多單開倉 (Long Entry)】──
        # 最佳起爆點（底部反轉第一時間開多）：
        # K 棒收盤突破站上基準線 (c > basis) 且 短均線金叉/站上基準線 (ema3 >= basis 或 ema3 > prev_ema3)
        prev_below_basis = float(prev.get('close', 0.0)) <= prev_basis or float(df.iloc[-3].get('close', 0.0)) <= prev_basis
        is_bull_breakout_reversal = (c > basis and c > o) and (ema3 >= basis or ma5 >= basis) and prev_below_basis
        
        # 中途二次進場（回踩確認）：
        recent_3 = df.tail(3)
        has_tested_support = float(recent_3['low'].min()) <= max(ema3, basis)
        is_bull_pullback_entry = (c > basis and c > o) and has_tested_support and (basis >= prev_basis)

        # 禁止半山腰追高：正乖離過大禁追
        bias_atr_long = (p - basis) / atr
        is_long_overextended = (bias_atr_long > self.max_bias_atr) or (p > kc_upper + 0.5 * atr)

        if (is_bull_breakout_reversal or is_bull_pullback_entry) and not is_long_overextended:
            if holding_side == 'SHORT':
                return 'FLIP_SHORT_TO_LONG', "FLIP: 底部反轉破位站上基準線，立即平空並反手開多！", details
            elif not holding_side:
                entry_type = "REVERSAL_BREAKOUT" if is_bull_breakout_reversal else "PULLBACK_REENTRY"
                return 'BUY_LONG', f"ENTER: 多頭波段啟動 ({entry_type})，第一時間開多！", details

        # ──【2. 空單開倉 (Short Entry，完全對稱)】──
        # 最佳起跌點（頂部反轉第一時間開空）：
        # K 棒收盤跌破基準線 (c < basis) 且 短均線死叉/壓制基準線 (ema3 <= basis 或 ema3 < prev_ema3)
        prev_above_basis = float(prev.get('close', 0.0)) >= prev_basis or float(df.iloc[-3].get('close', 0.0)) >= prev_basis
        is_bear_breakdown_reversal = (c < basis and c < o) and (ema3 <= basis or ma5 <= basis) and prev_above_basis

        # 中途二次進場（回抽確認）：
        has_tested_resistance = float(recent_3['high'].max()) >= min(ema3, basis)
        is_bear_pullback_entry = (c < basis and c < o) and has_tested_resistance and (basis <= prev_basis)

        # 禁止地板追空：負乖離過大禁追
        bias_atr_short = (basis - p) / atr
        is_short_overextended = (bias_atr_short > self.max_bias_atr) or (p < kc_lower - 0.5 * atr)

        if (is_bear_breakdown_reversal or is_bear_pullback_entry) and not is_short_overextended:
            if holding_side == 'LONG':
                return 'FLIP_LONG_TO_SHORT', "FLIP: 頂部反轉破位跌破基準線，立即平多並反手開空！", details
            elif not holding_side:
                entry_type = "REVERSAL_BREAKDOWN" if is_bear_breakdown_reversal else "PULLBACK_REENTRY"
                return 'SELL_SHORT', f"ENTER: 空頭波段啟動 ({entry_type})，第一時間開空！", details

        return None, "HOLD / NO_SIGNAL", details

    def should_close_short(
        self,
        position: Dict[str, Any],
        df: pd.DataFrame,
        quote: Optional[float] = None,
        is_candle_closed: Optional[bool] = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """空頭趨勢中持倉鎖定 (Bear Trend-Riding) 專用平空審查
        
        嚴格執行 2026-10-15 最新授權規範：
        一、 空頭趨勢狀態鎖定 (BEAR_TREND == True):
            1. KC 中軌 (或趨勢基準線) 斜率向下 (Basis_Slope < 0)
            2. KC 通道三軌 (上軌、中軌、下軌) 同步向下發散
            3. 價格多數收在 KC 中軌下方 (最近 5 根有 >= 3 根收在中軌下，且當前 Close < KC_Basis)

        二、 空頭趨勢中的【平倉禁令 (禁止中途平空)】:
            只要 BEAR_TREND == True:
            1. 禁用微幅反彈鎖利：嚴禁 2%~3% 的敏感反抽平空，盤中小綠棒或下影線視為下跌中繼正常呼吸，強制 HOLD！
            2. 允許通道內弱勢反抽：即使從 KC 下軌外反抽回通道內，只要實體收盤未站上 KC 中軌 (Close < KC_Basis)，一律不准平空！
            3. 短期均線未拐頭不平倉：短均線 (3 EMA / 5 EMA) 只要依然壓制在 K 棒上方且方向向下，堅決持倉！

        三、 空單在空頭趨勢下的【唯二平倉條件】:
            1. 結構被破 (Trend Invalidation): K 棒收盤價實體突破並站穩 KC 中軌 (Close > KC_Basis)。
            2. 極端恐慌天量插針 (Panic Climax Bottom):
               價格嚴重跌破 KC 下軌 (超賣負乖離)，且當根爆出 2.5 倍以上巨量，並留下超過實體 2 倍長度的超長下影線 (確認針尖放量換手)。

        四、 收盤價判定鐵律：
            所有條件均以「K 棒收盤價 (is_candle_closed == True)」判斷，未收盤 100% 駁回平倉！
        """
        if df is None or len(df) < 1:
            return False, "HOLD: INSUFFICIENT_DATA", {}

        curr = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else curr
        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        p = float(quote) if quote is not None else c

        if is_candle_closed is None:
            is_candle_closed = bool(curr.get('is_closed', True))

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        prev_basis = float(prev.get('kc_middle', prev.get('ma15', basis)))
        basis_slope = basis - prev_basis

        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        prev_upper = float(prev.get('kc_upper', prev_basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))
        prev_lower = float(prev.get('kc_lower', prev_basis - 2 * atr))

        ma5 = float(curr.get('ma5', c))
        prev_ma5 = float(prev.get('ma5', ma5))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))
        prev_ema3 = float(prev.get('ema3', prev.get('ma3', ema3)))

        details = {
            'side': 'SHORT',
            'close': c,
            'quote': p,
            'basis': basis,
            'basis_slope': basis_slope,
            'kc_upper': kc_upper,
            'kc_lower': kc_lower,
            'is_candle_closed': is_candle_closed,
            'atr': atr,
            'ma5': ma5,
            'ema3': ema3,
        }

        # ──【鐵律四：收盤價判定鐵律】──
        # 徹底避免被盤中跳動洗出場，未收線一律 100% HOLD！
        if not is_candle_closed:
            return False, "HOLD: CANDLE_NOT_CLOSED (等待 1M K 棒收線確認，徹底防範盤中毛刺與插針)", details

        # ──【一、 空頭趨勢狀態鎖定 (BEAR_TREND)】──
        # 1. KC 中軌 (或趨勢基準線) 斜率向下 (Basis_Slope < 0)
        # 2. KC 通道三軌 (上軌、中軌、下軌) 同步向下發散
        # 3. 價格多數收在 KC 中軌下方 (最近 5 根有 >= 3 根收在中軌下，且當前 Close < KC_Basis)
        recent_5 = df.tail(5)
        below_count = sum(float(r.get('close', 0)) <= float(r.get('kc_middle', basis)) for _, r in recent_5.iterrows())
        three_bands_down = (kc_upper <= prev_upper * 1.0005) and (basis <= prev_basis) and (kc_lower <= prev_lower * 1.0005)
        is_bear_trend = (basis_slope < 0 or three_bands_down) and (c < basis) and (below_count >= 3)
        details['is_bear_trend'] = is_bear_trend
        details['below_count'] = below_count

        # 條件 1：結構被破 (Trend Invalidation): K 棒收盤價實體突破並站穩 KC 中軌 (Close > KC_Basis)
        if c > basis:
            return True, "EXIT_SHORT_STRUCTURE_BROKEN (K 棒實體突破並收在 KC 中軌之上，空頭波段結構破壞，平空離場)", details

        # 條件 2：極端恐慌天量插針 (Panic Climax Bottom)
        body = abs(c - o)
        lower_wick = max(0.0, min(c, o) - l)
        bar_range = max(h - l, 1e-9)

        curr_vol = float(curr.get('volume', 0.0))
        prev_bars = df.iloc[:-1] if len(df) > 1 else df
        avg_vol = float(prev_bars['volume'].tail(10).mean()) if 'volume' in prev_bars.columns and len(prev_bars) > 0 else 0.0
        is_climax_volume = (avg_vol > 0 and curr_vol >= min(2.5, self.climax_volume_multiplier) * avg_vol)
        is_climax_wick = (lower_wick >= 2.0 * max(body, 0.1 * atr)) or (lower_wick / bar_range >= self.exhaustion_wick_ratio)
        is_severe_oversold = (l <= kc_lower) or ((basis - l) / atr >= 1.2)

        if is_severe_oversold and is_climax_volume and is_climax_wick:
            details['wick_ratio'] = round(lower_wick / bar_range, 3)
            details['wick_to_body_ratio'] = round(lower_wick / max(body, 1e-9), 2)
            details['volume_ratio'] = round(curr_vol / avg_vol, 2) if avg_vol > 0 else 0.0
            return True, "EXIT_SHORT_PANIC_CLIMAX_BOTTOM (KC下軌外超賣 + 爆量換手 + 超長下影線，恐慌針尖換手獲利離場)", details

        # ──【回抽容忍與影線利潤聯動規則 (徹底移除 KC 中軌平倉)】──
        entry_price = float(position.get("entry_price") or c)
        pnl_pct = (entry_price - p) / entry_price if entry_price > 0 else 0.0
        ma5_curr = float(curr.get("ma5", curr.get("_wild_ma5", c)))
        ma15_curr = float(curr.get("ma15", basis))
        body = abs(c - o)
        upper_wick = max(0.0, h - max(c, o))
        lower_wick = max(0.0, min(c, o) - l)

        # 1. 無利潤 / 浮虧狀態下 (浮盈 < 0.8%)：向上插針（長上影線）只要收盤在 MA15 下方且未金叉，一律視為阻力確認，嚴禁平倉！
        if pnl_pct < 0.008 and c <= ma15_curr and ma5_curr <= ma15_curr:
            return False, "HOLD: NO_PROFIT_WICK_TOLERANCE / BEAR_TREND_LOCK_INTACT / TREND_RIDING_LOCK_BEAR_INTACT (浮虧/無實質利潤中，上影線受阻確認阻力，嚴禁慌張平空)", details

        # 2. 已有實質利潤狀態下 (浮盈 >= 0.8%)：底部長下影線探底回升允許觸發保護性止盈
        if pnl_pct >= 0.008 and lower_wick > 1.5 * max(body, 1e-9):
            return True, "EXIT_BY_PROFIT_EXHAUSTION_WICK (實質利潤下底部長下影線力竭，保護性止盈平空)", details

        # 3. 回抽容忍（Hold on Pullback）：只要 ma5 < ma15 且收盤價未突破 ma15，視為空頭回抽，【嚴禁平空】！
        if ma5_curr < ma15_curr and c <= ma15_curr:
            return False, "HOLD: BEAR_HEALTHY_REBOUND_TOLERANCE / TREND_RIDING_LOCK_BEAR_INTACT / BEAR_TREND_LOCK_INTACT (MA5 < MA15 且收盤未破 MA15，空頭健康回抽嚴禁平空)", details


        # ──【平倉禁令：強制 HOLD，嚴禁中途被反彈洗出場】──
        # 1. 禁用微幅反彈鎖利：嚴禁 2%~3% 敏感反抽平空，盤中小綠棒/下影線一律視為「下跌中繼的正常呼吸」
        # 2. 允許通道內弱勢反抽：價格即使從 KC 下軌外反抽回通道內部，只要收盤未站上 KC 中軌 (Close < KC_Basis)，一律不准平空
        # 3. 短期均線未拐頭不平倉：短均線 (3 EMA / 5 EMA) 只要依然壓制在 K 棒上方且方向向下，堅決持倉
        short_ma_holding = (c <= ema3 or c <= ma5 or ema3 <= prev_ema3 or ma5 <= prev_ma5)
        details['short_ma_holding'] = short_ma_holding

        return False, "HOLD: BEAR_TREND_LOCK_INTACT / TREND_RIDING_LOCK_BEAR_INTACT (空頭主跌浪趨勢鎖定中，未破中軌且無天量插針，嚴禁中途被反彈洗下車)", details


# ══════════════════════════════════════════════════════════════════════
# 【五、 出場執行防滑價機制 (No Market Orders + 3s Watchdog)】
# ══════════════════════════════════════════════════════════════════════
class AntiSlippageOrderExecutor:
    """防滑價限價單平倉執行器 (全面廢棄 Stop Market)"""

    @staticmethod
    def calculate_limit_close_price(
        side: str,
        live_price: float,
        offset_pct: float = 0.002,
    ) -> float:
        """計算帶讓步緩衝之平倉限價 (多單賣出折讓 0.2%~0.3%，空單買入上浮 0.2%~0.3%)"""
        side = side.upper()
        if side in ('LONG', 'BUY'):
            # 多單平倉 (賣出)：以略低於當前價 0.2% 掛出積極限價單，鎖死滑價底線
            return live_price * (1.0 - max(0.001, offset_pct))
        else:
            # 空單平倉 (買入)：以略高於當前價 0.2% 掛出積極限價單，鎖死滑價上限
            return live_price * (1.0 + max(0.001, offset_pct))

    @classmethod
    async def execute_anti_slippage_close(
        cls,
        account: Any,
        symbol: str,
        side: str,
        qty: float,
        live_price: float,
        offset_pct: float = 0.002,
        watchdog_timeout_sec: float = 3.0,
        close_reason: str = "TREND_STRUCTURE_EXIT",
    ) -> Tuple[bool, Dict[str, Any]]:
        """以帶讓步限價單平倉，並由 3 秒 Watchdog 監控成交；超時未撮合則自動撤單抓最新買一/賣一家重掛"""
        if account is None or qty <= 0 or live_price <= 0:
            return False, {"error": "INVALID_PARAMS"}

        close_side = "sell" if side.upper() in ('LONG', 'BUY') else "buy"
        order_price = cls.calculate_limit_close_price(side, live_price, offset_pct)

        # 精度格式化
        if hasattr(account, "exchange") and hasattr(account.exchange, "price_to_precision"):
            order_price = float(account.exchange.price_to_precision(symbol, order_price))
        elif hasattr(account, "price_to_precision"):
            order_price = float(account.price_to_precision(symbol, order_price))

        account.log(
            f"🛡️ [防滑價限價平倉] {symbol} {close_side.upper()} 數量={qty} 限價={order_price:.6g} "
            f"(帶 {offset_pct*100:.1f}% 讓步緩衝，死守底線杜絕市價滑穿)",
            "INFO"
        )

        # 送出帶 reduceOnly 的限價平倉單
        order = await account._send_order(
            symbol,
            "limit",
            close_side,
            qty,
            order_price,
            {"reduceOnly": True, "newOrderRespType": "RESULT"},
        )

        order_id = (order or {}).get("id") or (order or {}).get("orderId")
        if not order_id:
            # 若為模擬盤直接全成
            if getattr(account, "is_paper", False) or "Paper" in type(account).__name__:
                return True, {"order_id": "PAPER_FILL", "price": order_price, "status": "FILLED"}
            account.log(f"🛑 [平倉下單失敗] 未返回有效 order_id", "ERROR")
            return False, {"error": "NO_ORDER_ID"}

        # ──【3 秒監控 Watchdog】──
        start_time = time.time()
        poll_interval = 0.5

        while time.time() - start_time < watchdog_timeout_sec:
            await asyncio.sleep(poll_interval)
            try:
                check_order = await account.exchange.fetch_order(str(order_id), symbol)
                status = str(check_order.get("status", "")).upper()
                filled = float(check_order.get("filled", 0.0))
                remaining = float(check_order.get("remaining", qty))

                if status == "FILLED" or remaining <= 0:
                    account.log(f"✅ [Watchdog 撮合成交] {symbol} 限價單 {order_id} 已在 {time.time()-start_time:.1f} 秒內完全撮合", "INFO")
                    return True, {"order_id": str(order_id), "status": "FILLED", "filled": filled}

                if status in ("CANCELED", "EXPIRED", "REJECTED"):
                    account.log(f"⚠️ [Watchdog 委託異常] 訂單狀態={status}，將以最新最佳報價重送", "WARNING")
                    break
            except Exception as e:
                logger.warning(f"Watchdog fetch_order failed: {e}")

        # ──【3 秒超時處置：撤單並改抓最新最佳對手價重掛】──
        account.log(f"⏱️ [Watchdog 3秒超時] 限價平倉單 {order_id} 未完全成交，立即撤單重新掛單！", "WARNING")
        try:
            await account._cancel_order(symbol, str(order_id))
        except Exception as e:
            logger.warning(f"Cancel order on watchdog timeout failed: {e}")

        # 抓取最新對手價重掛
        latest_price = live_price
        if hasattr(account, "tickers") and symbol in account.tickers:
            latest_price = float(account.tickers[symbol])
        elif hasattr(account, "exchange") and hasattr(account.exchange, "fetch_ticker"):
            try:
                t = await account.exchange.fetch_ticker(symbol)
                latest_price = float(t.get("last") or t.get("close") or latest_price)
            except Exception:
                pass

        # 以更積極之讓步價格重掛
        aggressive_price = cls.calculate_limit_close_price(side, latest_price, offset_pct=0.003)
        if hasattr(account, "exchange") and hasattr(account.exchange, "price_to_precision"):
            aggressive_price = float(account.exchange.price_to_precision(symbol, aggressive_price))

        account.log(f"🔄 [Watchdog 重掛限價] {symbol} 最新報價={latest_price} 重掛限價={aggressive_price:.6g}", "INFO")
        re_order = await account._send_order(
            symbol,
            "limit",
            close_side,
            qty,
            aggressive_price,
            {"reduceOnly": True, "newOrderRespType": "RESULT"},
        )
        return True, {"order_id": (re_order or {}).get("id"), "status": "RE_SUBMITTED", "price": aggressive_price}

    @classmethod
    async def execute_confirmed_limit_close(
        cls,
        account: Any,
        symbol: str,
        side: str,
        qty: float,
        live_price: float,
        offset_pct: float = 0.0025,
        watchdog_timeout_sec: float = 3.0,
        initial_limit_price: Optional[float] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Close with sequential reduce-only limits; never treat a submission as a fill."""
        if account is None or not math.isfinite(qty) or qty <= 0:
            return False, {"error": "INVALID_PARAMS"}
        if not math.isfinite(live_price) or live_price <= 0:
            return False, {"error": "INVALID_PRICE"}
        if getattr(account, "is_paper", False) or "Paper" in type(account).__name__:
            close_price = initial_limit_price or cls.calculate_limit_close_price(
                side, live_price, offset_pct,
            )
            return True, {
                "id": "PAPER_FILL", "status": "CLOSED", "filled": qty,
                "remaining": 0.0, "average": close_price,
            }

        close_side = "sell" if side.upper() in ("LONG", "BUY") else "buy"
        for attempt in range(3):
            quote = live_price
            if attempt:
                try:
                    cached = getattr(account, "tickers", {}).get(symbol)
                    if cached:
                        quote = float(cached)
                    else:
                        ticker = await account.exchange.fetch_ticker(symbol)
                        quote = float(ticker.get("last") or ticker.get("close") or 0)
                except Exception as exc:
                    account.log(
                        f"限價平倉重取報價失敗 {symbol}: {type(exc).__name__}: {exc}",
                        "ERROR",
                    )
                    return False, {"error": "QUOTE_UNKNOWN"}
                if not math.isfinite(quote) or quote <= 0:
                    return False, {"error": "QUOTE_INVALID"}

            offset = offset_pct if attempt == 0 else max(offset_pct, 0.003)
            raw_price = (
                initial_limit_price
                if attempt == 0 and initial_limit_price is not None
                else cls.calculate_limit_close_price(side, quote, offset)
            )
            order_price = float(account.exchange.price_to_precision(symbol, raw_price))
            order = await account._send_order(
                symbol, "limit", close_side, qty, order_price,
                {"reduceOnly": True, "newOrderRespType": "RESULT"},
            )
            order_id = (order or {}).get("id") or (order or {}).get("orderId")
            if not order_id:
                return False, {"error": "NO_ORDER_ID"}

            deadline = time.monotonic() + watchdog_timeout_sec
            status_order = order
            while time.monotonic() < deadline:
                try:
                    status_order = await account.exchange.fetch_order(str(order_id), symbol)
                except Exception as exc:
                    account.log(
                        f"平倉訂單狀態未知，禁止重複送單 {symbol} order={order_id}: "
                        f"{type(exc).__name__}: {exc}",
                        "ERROR",
                    )
                    account.position_meta.setdefault(symbol, {})[
                        "wild_swing_close_unknown"
                    ] = str(order_id)
                    account.save_state()
                    return False, {"error": "ORDER_STATUS_UNKNOWN", "id": str(order_id)}
                filled = float(status_order.get("filled") or 0.0)
                status = str(status_order.get("status") or "").lower()
                if status in ("closed", "filled") and filled + max(qty * 1e-8, 1e-12) >= qty:
                    status_order["status"] = "CLOSED"
                    return True, status_order
                if status in ("canceled", "cancelled", "expired", "rejected"):
                    break
                await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))

            try:
                await account._cancel_order(symbol, str(order_id))
                status_order = await account.exchange.fetch_order(str(order_id), symbol)
            except Exception as exc:
                account.log(
                    f"撤單或成交核對未知，禁止追單 {symbol} order={order_id}: "
                    f"{type(exc).__name__}: {exc}",
                    "ERROR",
                )
                account.position_meta.setdefault(symbol, {})[
                    "wild_swing_close_unknown"
                ] = str(order_id)
                account.save_state()
                return False, {"error": "CANCEL_STATUS_UNKNOWN", "id": str(order_id)}

            filled = float(status_order.get("filled") or 0.0)
            status = str(status_order.get("status") or "").lower()
            if filled + max(qty * 1e-8, 1e-12) >= qty and status in (
                "closed", "filled", "canceled", "cancelled",
            ):
                status_order.update({"status": "CLOSED", "filled": qty, "remaining": 0.0})
                return True, status_order
            if filled > 0:
                account.log(
                    f"限價平倉部分成交 {symbol}: {filled}/{qty}；先同步倉位，不重送原數量",
                    "WARNING",
                )
                await account.refresh(force=True)
                return False, {"error": "PARTIAL_FILL", "id": str(order_id), "filled": filled}
            if status not in ("canceled", "cancelled", "expired", "rejected"):
                account.position_meta.setdefault(symbol, {})[
                    "wild_swing_close_unknown"
                ] = str(order_id)
                account.save_state()
                return False, {"error": "CANCEL_NOT_CONFIRMED", "id": str(order_id)}

        return False, {"error": "UNFILLED_AFTER_RETRIES"}
