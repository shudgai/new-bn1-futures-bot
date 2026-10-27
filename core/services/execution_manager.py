"""
core/services/execution_manager.py
==================================
三層流水線之【執行層 (Execution Layer，唯一通道)】
職責：
- 全專案唯一合法的開平倉下單執行器。
- 嚴格封閉外部任意模組直接呼叫交易所下單 API。
- 主循環每秒跑標準流程：
  1. 抓取數據與計算指標 (DataPipeline)
  2. 若有持倉：只問 ExitGate -> 若觸發則呼叫 broker.close_position
  3. 若無持倉：只問 EntryGate -> 若通過則呼叫 broker.open_position
"""
import logging
import math
import time
from typing import Dict, Any, Optional
import pandas as pd

from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.gates.unified_pipeline_gates import ExitGate, EntryGate

logger = logging.getLogger("ExecutionManager")


# =========================================================================
# 🟢 1. 多頭持倉平倉判定 (峰頂生成 OR 結構破壞 -> 平多離場)
# =========================================================================
def should_exit_long(
    curr_k, prev_k, ma3_curr, ma3_prev, ma15_curr, ma15_prev, kc_upper, prev_kc_upper, current_atr,
    raw_df=None, position=None, swing_low_price=None,
):
    # 計算本波上漲的真實波段起漲低點 (Swing Low / Bottom Pivot)
    if swing_low_price is None:
        entry_price = float((position or {}).get("entry_price", 0.0) or 0.0)
        lowest_p = float((position or {}).get("lowest_price", 0.0) or 0.0)
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
            lookback = min(len(raw_df), 45)
            swing_low_price = float(raw_df['low'].iloc[-lookback:].min())
        else:
            swing_low_price = float(prev_k.get('low', curr_k.get('low', 0.0)))
        if entry_price > 0:
            swing_low_price = min(swing_low_price, entry_price)
        if lowest_p > 0:
            swing_low_price = min(swing_low_price, lowest_p)

    curr_close = float(curr_k['close'])
    curr_open = float(curr_k['open'])
    curr_body = abs(curr_close - curr_open)
    prev_close = float(prev_k['close'])
    prev_open = float(prev_k['open'])
    prev_body = abs(prev_close - prev_open)
    upper_wick = float(curr_k['high']) - max(curr_open, curr_close)
    atr_val = float(current_atr or 0.0)
    m3_c, m3_p = float(ma3_curr), float(ma3_prev)
    m15_c, m15_p = float(ma15_curr), float(ma15_prev)
    kc_u, p_kc_u = float(kc_upper), float(prev_kc_upper)

    # 【峰頂生成條件 A】：MA3 快線向下死叉 MA15 慢線
    is_ma3_dead_cross = (m3_c < m15_c) and (m3_p >= m15_p)
    
    # 【峰頂生成條件 B】：極限見頂反轉 (軌外大陰吞噬 >= 60% OR 高位長上影天地針)
    is_engulfing_peak = (
        (prev_close > p_kc_u)
        and (curr_close < curr_open)
        and (curr_body >= 0.6 * prev_body)
    )
    is_pinbar_peak = (
        (upper_wick >= 1.2 * curr_body)
        and (curr_close < curr_open)
        and (float(curr_k['high']) >= kc_u)
    )
    is_peak_signal = is_engulfing_peak or is_pinbar_peak

    # 【峰頂生成條件 C】：Pre-Cross 峰頂拐頭 (KC 上半區 + MA3 向下拐頭 + 實體陰線收盤跌破 MA3)
    # KC 上半區：close >= (kc_upper - (kc_upper - ma15_curr) * 0.5)
    kc_upper_half = kc_u - (kc_u - m15_c) * 0.5
    is_pivot_peak = (
        (curr_close >= kc_upper_half)
        and (m3_c < m3_p)
        and (curr_close < curr_open)
        and (curr_close <= m3_c)
    )

    # 峰頂力竭/轉折訊號綜合判定
    is_peak_formed = is_ma3_dead_cross or is_peak_signal or is_pivot_peak

    # 【極端崩盤熔斷】：單根大陰線實體 body >= 1.8 ATR 且貫穿 MA15 慢線
    is_crash_circuit_breaker = (
        (curr_body >= 1.8 * atr_val)
        and (curr_close < m15_c)
    )

    # 【最終底線】：收盤價實體跌破本波波段起漲低點 Swing Low (真正的結構破壞)
    is_structure_broken = (curr_close < float(swing_low_price)) and (curr_close < m15_c)

    # ⚠️ 只要「生成峰頂」、「極端熔斷」或「結構破壞」，立即觸發平多！
    if is_peak_formed or is_crash_circuit_breaker or is_structure_broken:
        return True

    return False  # 未達峰頂且未破壞結構，繼續持倉續抱拿滿波段


# =========================================================================
# 🔴 2. 空頭持倉平倉判定 (谷底生成 OR 結構破壞 -> 平空離場)
# =========================================================================
def should_exit_short(
    curr_k, prev_k, ma3_curr, ma3_prev, ma15_curr, ma15_prev, kc_lower, prev_kc_lower, current_atr,
    raw_df=None, position=None, swing_high_price=None,
):
    # 計算本波下跌的真實波段起跌高點 (Swing High / Top Pivot)
    if swing_high_price is None:
        entry_price = float((position or {}).get("entry_price", 0.0) or 0.0)
        highest_p = float((position or {}).get("highest_price", 0.0) or 0.0)
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
            lookback = min(len(raw_df), 45)
            swing_high_price = float(raw_df['high'].iloc[-lookback:].max())
        else:
            swing_high_price = float(prev_k.get('high', curr_k.get('high', 0.0)))
        if entry_price > 0:
            swing_high_price = max(swing_high_price, entry_price)
        if highest_p > 0:
            swing_high_price = max(swing_high_price, highest_p)

    curr_close = float(curr_k['close'])
    curr_open = float(curr_k['open'])
    curr_body = abs(curr_close - curr_open)
    prev_close = float(prev_k['close'])
    prev_open = float(prev_k['open'])
    prev_body = abs(prev_close - prev_open)
    lower_wick = min(curr_open, curr_close) - float(curr_k['low'])
    atr_val = float(current_atr or 0.0)
    m3_c, m3_p = float(ma3_curr), float(ma3_prev)
    m15_c, m15_p = float(ma15_curr), float(ma15_prev)
    kc_l, p_kc_l = float(kc_lower), float(prev_kc_lower)

    # 【谷底生成條件 A】：MA3 快線向上金叉 MA15 慢線
    is_ma3_golden_cross = (m3_c > m15_c) and (m3_p <= m15_p)
    
    # 【谷底生成條件 B】：極限見底反轉 (軌外大陽吞噬 >= 60% OR 低位長下影刺針)
    is_engulfing_bottom = (
        (prev_close < p_kc_l)
        and (curr_close > curr_open)
        and (curr_body >= 0.6 * prev_body)
    )
    is_pinbar_bottom = (
        (lower_wick >= 1.2 * curr_body)
        and (curr_close > curr_open)
        and (float(curr_k['low']) <= kc_l)
    )
    is_bottom_signal = is_engulfing_bottom or is_pinbar_bottom

    # 【谷底生成條件 C】：Pre-Cross 谷底拐頭 (KC 下半區 + MA3 向上拐頭 + 實體陽線收盤站上 MA3)
    # KC 下半區：close <= (kc_lower + (ma15_curr - kc_lower) * 0.5)
    kc_lower_half = kc_l + (m15_c - kc_l) * 0.5
    is_pivot_bottom = (
        (curr_close <= kc_lower_half)
        and (m3_c > m3_p)
        and (curr_close > curr_open)
        and (curr_close >= m3_c)
    )

    # 谷底力竭/轉折訊號綜合判定
    is_bottom_formed = is_ma3_golden_cross or is_bottom_signal or is_pivot_bottom

    # 【極端爆發熔斷】：單根大陽線實體 body >= 1.8 ATR 且貫穿 MA15 慢線
    is_spike_circuit_breaker = (
        (curr_body >= 1.8 * atr_val)
        and (curr_close > m15_c)
    )

    # 【最終底線】：收盤價實體突破本波波段起跌高點 Swing High (真正的結構破壞)
    is_structure_broken = (curr_close > float(swing_high_price)) and (curr_close > m15_c)

    # ⚠️ 只要「生成谷底」、「極端熔斷」或「結構破壞」，立即觸發平空！
    if is_bottom_formed or is_spike_circuit_breaker or is_structure_broken:
        return True

    return False  # 未達谷底且未破壞結構，繼續持倉續抱拿滿波段


class ExecutionManager:
    """唯一開平倉執行通道與標準流水線協調器"""

    @classmethod
    async def process_symbol_pipeline(
        cls,
        symbol: str,
        engine_or_account: Any,
        klines_df: Optional[pd.DataFrame] = None,
        quote: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        單一幣種的標準流水線處理循環 (Thread-safe, Single-Pipeline)
        """
        account = getattr(engine_or_account, "account", engine_or_account)

        # 1. 抓取數據與計算純數值指標 (Data Layer)
        current_position = account.positions.get(symbol)
        has_position = bool(current_position and abs(float(current_position.get("amount", 0.0) or current_position.get("qty", 0.0))) > 0)

        if klines_df is None or klines_df.empty:
            if hasattr(engine_or_account, "fetch_klines"):
                try:
                    klines_df = await engine_or_account.fetch_klines(symbol, timeframe="1m", limit=60)
                except Exception as e:
                    logger.debug(f"[PIPELINE] {symbol} fetch_klines 失敗: {e}")
                    return {"status": "FETCH_FAILED", "symbol": symbol}
            else:
                return {"status": "NO_KLINES", "symbol": symbol}

        market_data: Optional[MarketDataSnapshot] = DataPipeline.calculate_snapshot(
            symbol=symbol,
            df=klines_df,
            current_price=quote,
            position=current_position if has_position else None,
        )
        if not market_data:
            if not has_position:
                return {"status": "SNAPSHOT_FAILED", "symbol": symbol}

            # 既有持倉：快照失敗不得盲目停止風險管理！啟用緊急防禦判斷
            logger.warning(f"⚠️ [DATA_OUTAGE] {symbol} 快照計算失敗但有既有持倉，啟動持倉安全處置通道！")
            pos_entry = float(current_position.get("entry_price") or 0.0)
            pos_side = str(current_position.get("side") or "").upper()
            pos_leverage = float(current_position.get("leverage") or 1.0)

            # 核對是否具備可信報價
            if quote and quote > 0 and pos_entry > 0:
                pnl_pct = (quote - pos_entry) / pos_entry if pos_side == "LONG" else (pos_entry - quote) / pos_entry
                unrealized_roe = pnl_pct * pos_leverage

                # 保守估計 3 ATR 止損距離
                fallback_atr = pos_entry * 0.015
                hard_sl_threshold = -3.0 * (fallback_atr / pos_entry)

                # 若虧損超過硬止損底線，立即緊急平倉避險
                if pnl_pct <= hard_sl_threshold or unrealized_roe <= -0.5:
                    logger.critical(
                        f"🚨 [EMERGENCY_DATA_OUTAGE_CLOSE] {symbol} 快照失效期間觸發緊急硬止損！"
                        f"pnl_pct={pnl_pct:.2%}, roe={unrealized_roe:.2%}"
                    )
                    success = await cls.close_position(
                        account=account,
                        symbol=symbol,
                        price=quote,
                        reason="CIRCUIT_BREAKER_EMERGENCY_DATA_OUTAGE",
                        meta={"cause": "Snapshot failed with emergency loss threshold breach"}
                    )
                    return {"status": "EMERGENCY_EXIT_EXECUTED", "symbol": symbol, "success": success}

                logger.info(f"🛡️ [DATA_OUTAGE_HOLD] {symbol} 資料快照失敗，但即時報價未觸發緊急硬止損 (roe={unrealized_roe:.2%})，依交易所原生保護單守護持倉")
                return {"status": "DATA_OUTAGE_HOLD", "symbol": symbol, "quote": quote}
            else:
                logger.critical(f"🛑 [CRITICAL_DATA_OUTAGE] {symbol} 快照與即時報價均失效！嚴禁增加風險，請依交易所原生止損守護持倉")
                return {"status": "CRITICAL_DATA_OUTAGE", "symbol": symbol}

        if has_position:
            from core.services.exits.trend_lock import update_trend_lock_state
            if update_trend_lock_state(current_position, klines_df):
                meta = getattr(account, "position_meta", {}).setdefault(symbol, {})
                meta["trend_lock_state"] = dict(current_position["trend_lock_state"])
                save = getattr(account, "save_state", None)
                if callable(save):
                    save()

        # 2. 若有持倉：只問 ExitGate (唯一裁決官)
        if has_position:
            should_exit, reason, meta = ExitGate.evaluate(
                pos=current_position,
                market_data=market_data,
            )
            if should_exit:
                pos_side = str(current_position.get("side", "")).upper()
                raw_df = getattr(market_data, "raw_df", None)
                c_k = market_data.curr_k if market_data.curr_k else (raw_df.iloc[-1] if raw_df is not None and len(raw_df) >= 1 else {})
                p_k = market_data.prev_k if market_data.prev_k else (raw_df.iloc[-2] if raw_df is not None and len(raw_df) >= 2 else c_k)
                c_c = float(c_k.get("close", market_data.current_price))
                c_o = float(c_k.get("open", c_c))
                c_h = float(c_k.get("high", max(c_c, c_o)))
                c_l = float(c_k.get("low", min(c_c, c_o)))
                p_c = float(p_k.get("close", c_o))
                p_o = float(p_k.get("open", p_c))
                p_h = float(p_k.get("high", max(p_c, p_o)))
                p_l = float(p_k.get("low", min(p_c, p_o)))

                m3_c = float(c_k.get("ma3", getattr(market_data, "ma3", c_c)))
                m3_p = float(p_k.get("ma3", getattr(market_data, "prev_ma3", m3_c)))
                m15_c = float(c_k.get("ma15", getattr(market_data, "ma15", c_c)))
                m15_p = float(p_k.get("ma15", getattr(market_data, "prev_ma15", m15_c)))

                kc_u = float(c_k.get("kc_upper", market_data.kc_upper))
                p_kc_u = float(p_k.get("kc_upper", kc_u))
                kc_lo = float(c_k.get("kc_lower", market_data.kc_lower))
                p_kc_lo = float(p_k.get("kc_lower", kc_lo))

                c_bd = abs(c_c - c_o)
                p_bd = abs(p_c - p_o)
                c_up_w = c_h - max(c_c, c_o)
                c_lo_w = min(c_c, c_o) - c_l
                atr_v = float(getattr(market_data, "atr", 0.0) or 0.0)
                kc_slope = float(getattr(market_data, "kc_middle_slope", 0.0) or 0.0)

                # ══════════════════════════════════════════════════════════════════
                # 🟢 多單/空單持倉抗洗盤最外層硬性卡死（未滿足極限峰谷條件，絕對禁止平倉）
                if reason not in ExitGate.ABSOLUTE_SAFETY_REASONS:
                    if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 2:

                        if pos_side == "LONG":
                            can_exit = should_exit_long(
                                curr_k=c_k, prev_k=p_k,
                                ma3_curr=m3_c, ma3_prev=m3_p,
                                ma15_curr=m15_c, ma15_prev=m15_p,
                                kc_upper=kc_u, prev_kc_upper=p_kc_u,
                                current_atr=atr_v,
                                raw_df=klines_df,
                                position=current_position,
                            )
                            if not can_exit:
                                logger.info(
                                    f"🛡️ [TREND_LOCK_MASTER_VETO] {symbol} 多單收盤價(close={c_c:.6f})穩守支撐 (MA3={m3_c:.6f}, MA15={m15_c:.6f}, slope={kc_slope:.6f})，"
                                    f"未滿足四大極限峰頂門檻，一票否決平多，強制持倉續抱拿滿主升段！(阻斷理由: {reason})"
                                )
                                return {"status": "HOLD", "symbol": symbol, "reason": "TREND_LOCK_MASTER_LOCK_LONG"}

                        elif pos_side == "SHORT":
                            can_exit = should_exit_short(
                                curr_k=c_k, prev_k=p_k,
                                ma3_curr=m3_c, ma3_prev=m3_p,
                                ma15_curr=m15_c, ma15_prev=m15_p,
                                kc_lower=kc_lo, prev_kc_lower=p_kc_lo,
                                current_atr=atr_v,
                                raw_df=klines_df,
                                position=current_position,
                            )
                            if not can_exit:
                                logger.info(
                                    f"🛡️ [TREND_LOCK_MASTER_VETO] {symbol} 空單收盤價(close={c_c:.6f})壓制在阻力之下 (MA3={m3_c:.6f}, MA15={m15_c:.6f}, slope={kc_slope:.6f})，"
                                    f"未滿足四大極限谷底門檻，一票否決平空，強制持倉續抱拿滿主跌段！(阻斷理由: {reason})"
                                )
                                return {"status": "HOLD", "symbol": symbol, "reason": "TREND_LOCK_MASTER_LOCK_SHORT"}

                logger.info(f"🚀 [EXIT_TRIGGERED] {symbol} {reason} | meta={meta}")
                print(f"🚀 [EXIT_TRIGGERED] {symbol} {reason}", flush=True)
                exit_price = market_data.current_price
                exit_meta = {**(meta or {}), "raw_df": getattr(market_data, "raw_df", None)}
                success = await cls.close_position(
                    account=account,
                    symbol=symbol,
                    price=exit_price,
                    reason=f"ExitGate:{reason}",
                    meta=exit_meta,
                )
                if not success:
                    return {"status": "EXIT_FAILED", "symbol": symbol, "reason": reason}

                # ══════════════════════════════════════════════════════════════════
                # 🔄 關鍵秒級反手邏輯 (Flip Position): 平倉與反手在同根 K 棒一次性完成！
                # ══════════════════════════════════════════════════════════════════
                # 🔴 空頭平倉 -> 谷底金叉秒級反手開多 (Flip to Long)
                is_ma3_golden_cross = (m3_c > m15_c) and (m3_p <= m15_p)
                is_bullish_confirm = (c_c > c_o) and (m3_c > m3_p)
                is_flip_to_long = (pos_side == "SHORT" and is_ma3_golden_cross and is_bullish_confirm)

                # 🟢 多頭平倉 -> 頂部死叉秒級反手開空 (Flip to Short)
                is_ma3_dead_cross = (m3_c < m15_c) and (m3_p >= m15_p)
                is_bearish_confirm = (c_c < c_o) and (m3_c < m3_p)
                is_flip_to_short = (pos_side == "LONG" and is_ma3_dead_cross and is_bearish_confirm)

                if is_flip_to_long or is_flip_to_short:
                    flip_side = "LONG" if is_flip_to_long else "SHORT"
                    flip_entry_type = "PIVOT_FLIP_LONG" if is_flip_to_long else "PIVOT_FLIP_SHORT"
                    logger.info(f"🔄 [POSITION_FLIP] {symbol} {pos_side} 平倉後同 K 棒即刻秒級反手開 {flip_side}！(entry_type={flip_entry_type})")
                    print(f"🔄 [POSITION_FLIP] {symbol} {pos_side} 平倉後同 K 棒即刻秒級反手開 {flip_side}！", flush=True)

                    flip_stop_loss = (
                        exit_price - 3.0 * atr_v if flip_side == "LONG"
                        else exit_price + 3.0 * atr_v
                    )
                    flip_meta = {
                        "side": flip_side,
                        "direction_override": True,
                        "entry_type": flip_entry_type,
                        "stop_loss_price": flip_stop_loss,
                        "upstream_entry_gate_authorized": True,
                        "allow_kc_internal": True,
                        "strategy_mode": "AGILE_MOMENTUM",
                        "raw_df": klines_df,
                        "source": f"UNIFIED_PIPELINE|{flip_entry_type}",
                        "atr": atr_v,
                    }
                    raw_df_instance = flip_meta.get("raw_df")
                    if hasattr(raw_df_instance, "attrs"):
                        for k in ("direction_override", "is_agile_trigger", "is_cross_mid", "entry_type", "strategy_mode", "allow_kc_internal"):
                            raw_df_instance.attrs[k] = flip_meta.get(k, True)

                    flip_success = await cls.open_position(
                        engine_or_account=engine_or_account,
                        symbol=symbol,
                        side=flip_side,
                        price=exit_price,
                        reason=f"FlipPosition:{flip_entry_type}",
                        meta=flip_meta,
                    )
                    if flip_success:
                        logger.info(f"🎯 [POSITION_FLIP_SUCCESS] {symbol} 成功秒級反手建立 {flip_side} 倉位！")
                        print(f"🎯 [POSITION_FLIP_SUCCESS] {symbol} 成功秒級反手建立 {flip_side} 倉位！", flush=True)
                        return {"status": "SUCCESS_FLIP", "symbol": symbol, "side": flip_side}

                # 平倉成功且未觸發秒級反手時，重設持倉標記直通第 3 階段評估開倉
                has_position = False
                current_position = None
            else:
                logger.debug(f"🛡️ [HOLD] {symbol} {reason}")
                return {"status": "HOLD", "symbol": symbol, "reason": reason}

        # 3. 若無持倉或剛執行平倉：直通評估開倉或即刻反手開倉 (Flip Position)
        if not has_position:
            # 偵測候選方向訊號 (大週期順勢原則：多頭大週期只看多、空頭大週期只看空，絕不頻繁多空互反)
            candidate_signal = None
            ma5_curr = float(market_data.curr_k.get("ma5", market_data.current_price))
            ma5_prev = float(market_data.prev_k.get("ma5", ma5_curr))
            ma15_curr = float(market_data.curr_k.get("ma15", getattr(market_data, "ma15", ma5_curr)))
            ma15_prev = float(market_data.prev_k.get("ma15", ma15_curr))
            ma3_curr = float(market_data.curr_k.get("ma3", getattr(market_data, "ma3", market_data.current_price)))
            ma3_prev = float(market_data.prev_k.get("ma3", getattr(market_data, "prev_ma3", ma3_curr)))
            ma3_slope = ma3_curr - ma3_prev
            macro_trend = getattr(market_data, "macro_trend", "NEUTRAL")
            raw_df = getattr(market_data, "raw_df", None)
            live_cross_long = None
            live_cross_short = None
            ma_trend_aligned_long = False
            ma_trend_aligned_short = False
            ma3_directional_long = False
            ma3_directional_short = False
            if raw_df is not None and len(raw_df) >= 2:
                from core.gates.pipeline import fast_cross_evidence
                from core.services.candle_data import closed_entry_candles

                closed_ma = closed_entry_candles(raw_df)
                if len(closed_ma) >= 2:
                    if not {"ma3", "ma15"}.issubset(closed_ma.columns):
                        closed_ma = closed_ma.copy()
                        closed_ma["ma3"] = closed_ma["close"].rolling(3, min_periods=1).mean()
                        closed_ma["ma15"] = closed_ma["close"].rolling(15, min_periods=1).mean()
                    prior_ma = closed_ma.iloc[-2]
                    latest_ma = closed_ma.iloc[-1]
                    ma3_prior = float(prior_ma["ma3"])
                    ma15_prior = float(prior_ma["ma15"])
                    ma3_latest = float(latest_ma["ma3"])
                    ma15_latest = float(latest_ma["ma15"])
                    live_row = raw_df.iloc[-1]
                    raw_close = float(live_row.get("close", market_data.current_price))
                    live_ma3 = float(live_row.get("ma3", ma3_latest))
                    live_bar_is_closed = bool(live_row.get("is_closed", True))
                    if not live_bar_is_closed:
                        live_ma3 += (float(market_data.current_price) - raw_close) / 3.0
                    long_ma3_rising = (
                        ma3_latest > ma3_prior
                        if live_bar_is_closed else live_ma3 > ma3_latest
                    )
                    short_ma3_falling = (
                        ma3_latest < ma3_prior
                        if live_bar_is_closed else live_ma3 < ma3_latest
                    )
                    ma_values = (ma3_prior, ma15_prior, ma3_latest, ma15_latest, live_ma3)
                    if all(math.isfinite(value) and value > 0 for value in ma_values):
                        ma3_curr = ma3_latest
                        ma3_prev = ma3_prior
                        ma3_slope = ma3_curr - ma3_prev
                        ma3_directional_long = ma3_latest > ma3_prior and long_ma3_rising
                        ma3_directional_short = ma3_latest < ma3_prior and short_ma3_falling
                        ma_trend_aligned_long = (
                            ma3_directional_long
                            and ma15_latest > ma15_prior
                            and ma3_latest > ma15_latest
                        )
                        ma_trend_aligned_short = (
                            ma3_directional_short
                            and ma15_latest < ma15_prior
                            and ma3_latest < ma15_latest
                        )
                live_cross_long = fast_cross_evidence(
                    raw_df, market_data.current_price, "LONG", symbol=symbol,
                )
                live_cross_short = fast_cross_evidence(
                    raw_df, market_data.current_price, "SHORT", symbol=symbol,
                )

            if not ma3_directional_long and ma5_curr > ma5_prev:
                ma3_directional_long = True
            if not ma3_directional_short and ma5_curr < ma5_prev:
                ma3_directional_short = True

            # Two closed candles authorize KC breakout entries: K1 opens
            # inside the channel and closes beyond the rail; K2 is same-color
            # and closes beyond both its KC rail and MA5. Revalidate live quote.
            two_bar_breakout = None
            if raw_df is not None:
                from core.services.candle_data import closed_entry_candles
                from core.services.kc_two_bar_entry import evaluate_kc_two_bar_entry

                two_bar_breakout = evaluate_kc_two_bar_entry(
                    closed_entry_candles(raw_df), market_data.current_price,
                )
            is_breakout_long = bool(two_bar_breakout and two_bar_breakout.get("side") == "LONG")
            is_breakdown_short = bool(two_bar_breakout and two_bar_breakout.get("side") == "SHORT")

            # 模式 2：趨勢回踩均線帶守穩轉強信號
            is_pullback_long = (
                float(market_data.curr_k.get("low", market_data.current_price)) <= ma5_curr * 1.002
                and market_data.curr_k["close"] >= ma5_curr
                and market_data.curr_k["close"] >= market_data.curr_k["open"]
                and ma5_curr >= ma5_prev
                and market_data.kc_middle_slope >= -0.000005
                and market_data.curr_k["close"] >= market_data.ema_slow
                and ma_trend_aligned_long
            )
            is_pullback_short = (
                float(market_data.curr_k.get("high", market_data.current_price)) >= ma5_curr * 0.998
                and market_data.curr_k["close"] <= ma5_curr
                and market_data.curr_k["close"] <= market_data.curr_k["open"]
                and ma5_curr <= ma5_prev
                and market_data.kc_middle_slope <= 0.000005
                and market_data.curr_k["close"] <= market_data.ema_slow
                and ma_trend_aligned_short
            )

            # MA3 交叉 KC 中軌進場授權（統一核心鐵律）：
            # 做多：前一根已收線 MA3 <= KC 中軌，當前已收線 MA3 > KC 中軌，當前 MA3 > 前一根 MA3 (斜率向上)
            # 做空：前一根已收線 MA3 >= KC 中軌，當前已收線 MA3 < KC 中軌，當前 MA3 < 前一根 MA3 (斜率向下)
            from core.gates.ma3_kc_midline_cross_gate import evaluate_ma3_kc_midline_cross
            from core.services.candle_data import closed_entry_candles

            closed_for_cross = closed_entry_candles(raw_df) if raw_df is not None else None
            ma3_cross_long_ok, ma3_cross_long_reason, ma3_cross_long_meta = (
                evaluate_ma3_kc_midline_cross(closed_for_cross, "LONG", symbol)
                if closed_for_cross is not None and len(closed_for_cross) >= 2
                else (False, "NO_CLOSED_BARS", {})
            )
            ma3_cross_short_ok, ma3_cross_short_reason, ma3_cross_short_meta = (
                evaluate_ma3_kc_midline_cross(closed_for_cross, "SHORT", symbol)
                if closed_for_cross is not None and len(closed_for_cross) >= 2
                else (False, "NO_CLOSED_BARS", {})
            )

            # 結合肯特納通道 (KC) 方向與大週期趨勢濾網：
            # 若主趨勢向上 (KC 中軌向上或大趨勢 BULLISH)，只做金叉開多，100% 禁止死叉開空！
            # 若主趨勢向下 (KC 中軌向下或大趨勢 BEARISH)，只做死叉開空，100% 禁止金叉開多！
            kc_slope = getattr(market_data, "kc_middle_slope", 0.0)
            is_macro_bull = (macro_trend == "BULLISH" or kc_slope > 0.000005)
            is_macro_bear = (macro_trend == "BEARISH" or kc_slope < -0.000005)

            is_ma_cross_long = (
                ma3_cross_long_ok
                and not is_macro_bear
                and kc_slope >= -0.000005
                and market_data.curr_k["close"] >= market_data.kc_middle
            )
            is_ma_cross_short = (
                ma3_cross_short_ok
                and not is_macro_bull
                and kc_slope <= 0.000005
                and market_data.curr_k["close"] <= market_data.kc_middle
            )

            # 順勢鐵律與波段判定 (常規追單防護)
            is_above_slow_ma = (market_data.curr_k["close"] >= market_data.ema_slow or market_data.current_price >= market_data.ema_slow)
            is_slow_ma_up = getattr(market_data, "slow_slope", 0.0) >= 0
            is_bull_alignment = (market_data.ema_fast >= market_data.ema_slow)

            is_below_slow_ma = (market_data.curr_k["close"] <= market_data.ema_slow or market_data.current_price <= market_data.ema_slow)
            is_slow_ma_down = getattr(market_data, "slow_slope", 0.0) <= 0
            is_bear_alignment = (market_data.ema_fast <= market_data.ema_slow)

            is_bullish_wave = (
                macro_trend == "BULLISH"
                or is_bull_alignment
                or (is_above_slow_ma and is_slow_ma_up)
                or (market_data.kc_middle_slope >= -0.000005 and market_data.curr_k["close"] >= market_data.kc_middle)
            )
            is_bearish_wave = (
                macro_trend == "BEARISH"
                or is_bear_alignment
                or (is_below_slow_ma and is_slow_ma_down)
                or (market_data.kc_middle_slope <= 0.000005 and market_data.curr_k["close"] <= market_data.kc_middle)
            )

            # 單一軌外延續開倉判定（2026-10-09 最新授權：平倉後或破軌沒開倉，只要上一根在軌外且當前報價在軌外及MA5外，順勢外軌鎖定延續開倉）
            p_up = float(market_data.prev_k.get("kc_upper", market_data.kc_upper) if market_data.prev_k else market_data.kc_upper)
            p_low = float(market_data.prev_k.get("kc_lower", market_data.kc_lower) if market_data.prev_k else market_data.kc_lower)
            p_close = float(market_data.prev_k.get("close", market_data.current_price) if market_data.prev_k else market_data.current_price)
            curr_quote = float(market_data.current_price)
            curr_c = float(market_data.curr_k.get("close", curr_quote))
            curr_o = float(market_data.curr_k.get("open", curr_c))

            p_open = float(market_data.prev_k.get("open", p_close) if market_data.prev_k else p_close)
            is_prev_pullback_for_short = p_close > p_open  # 前根為反彈小陽 K
            is_prev_pullback_for_long = p_close < p_open   # 前根為回踩小陰 K

            # 單一軌外延續開倉判定：
            # 若前根為回踩/反彈反向棒，嚴格禁止單根延續偷跑！必須等三棒實體確認！
            is_continuation_long = (
                not is_macro_bear
                and not is_prev_pullback_for_long
                and (p_close > p_up or curr_quote > market_data.kc_upper)
                and curr_quote > market_data.kc_upper
                and curr_quote > ma5_curr
                and ma5_curr > ma5_prev
                and kc_slope >= -0.000005
                and curr_c > curr_o
            )
            is_continuation_short = (
                not is_macro_bull
                and not is_prev_pullback_for_short
                and (p_close < p_low or curr_quote < market_data.kc_lower)
                and curr_quote < market_data.kc_lower
                and curr_quote < ma5_curr
                and ma5_curr < ma5_prev
                and kc_slope <= 0.000005
                and curr_c < curr_o
            )

            # 任務 3：順勢破軌回踩再起飛模式 (Breakout-Pullback-Continuation 三棒組合模式)
            # 嚴禁在 K2 反向小 K 當下偷跑開倉，必須等到 K3 實體收線確認突破 K2 高/低點！
            three_bar_long = False
            three_bar_short = False
            three_bar_stop = None
            if isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
                try:
                    from core.services.candle_data import closed_entry_candles
                    closed_bars = closed_entry_candles(raw_df)
                    cand_k1, cand_k2, cand_k3 = None, None, None
                    if len(closed_bars) >= 3:
                        cand_k1 = closed_bars.iloc[-3]
                        cand_k2 = closed_bars.iloc[-2]
                        cand_k3 = closed_bars.iloc[-1]
                    elif len(closed_bars) >= 2:
                        cand_k1 = closed_bars.iloc[-2]
                        cand_k2 = closed_bars.iloc[-1]
                        cand_k3 = market_data.curr_k

                    if cand_k1 is not None and cand_k2 is not None and cand_k3 is not None:
                        k1_c, k1_o = float(cand_k1["close"]), float(cand_k1["open"])
                        k2_c, k2_o = float(cand_k2["close"]), float(cand_k2["open"])
                        k3_c, k3_o = float(cand_k3["close"]), float(cand_k3["open"])
                        k2_h, k2_l = float(cand_k2["high"]), float(cand_k2["low"])
                        k1_body = abs(k1_c - k1_o)
                        k2_body = abs(k2_c - k2_o)
                        k1_up = float(cand_k1.get("kc_upper", 0.0))
                        k1_low = float(cand_k1.get("kc_lower", 0.0))
                        k2_ma3 = float(cand_k2.get("ma3", k2_c))

                        # 🟢 多頭二次起飛 (CONTINUATION_LONG) 嚴格判定：
                        # K1: 陽線衝破 KC 上軌
                        # K2: 縮量小陰線回踩，實體 <= 0.5 * K1，且收盤未跌破 MA3
                        # K3: 必須為【實體陽線】且收盤突破 K2 高點，且 MA3 當前明確向上！
                        is_k1_breakout = (k1_c > k1_o and k1_c > k1_up)
                        is_k2_pullback_long = (k2_c < k2_o and k2_body < 0.5 * k1_body and k2_c >= k2_ma3)
                        is_k3_confirm_long = (k3_c > k3_o and k3_c > k2_h and ma3_curr > ma3_prev)

                        if is_k1_breakout and is_k2_pullback_long and is_k3_confirm_long:
                            three_bar_long = True
                            three_bar_stop = float(k2_l)

                        # 🔴 空頭二次下殺 (CONTINUATION_SHORT) 嚴格判定：
                        # K1: 陰線跌破 KC 下軌
                        # K2: 縮量小陽線反彈，實體 <= 0.5 * K1，且收盤未突破 MA3
                        # K3: 必須為【實體紅 K/陰線】且收盤跌破 K2 低點，且 MA3 當前明確向下！
                        is_k1_breakdown = (k1_c < k1_o and k1_c < k1_low)
                        is_k2_pullback_short = (k2_c > k2_o and k2_body < 0.5 * k1_body and k2_c <= k2_ma3)
                        is_k3_confirm_short = (k3_c < k3_o and k3_c < k2_l and ma3_curr < ma3_prev)

                        if is_k1_breakdown and is_k2_pullback_short and is_k3_confirm_short:
                            three_bar_short = True
                            three_bar_stop = float(k2_h)
                except Exception:
                    pass

            # 盤整鎖定 (KC Squeeze Gate)：
            # 當 kc_width_ratio = (kc_upper - kc_lower) / kc_middle < 0.015 或 current_atr < 0.7 * avg_atr_20 時，
            # 標記為 MARKET_SQUEEZE，一票否決所有常規開倉
            kc_mid_val = float(market_data.kc_middle)
            kc_width = float(market_data.kc_upper) - float(market_data.kc_lower)
            kc_width_ratio = kc_width / kc_mid_val if kc_mid_val > 0 else 0.0
            curr_atr = float(getattr(market_data, "atr", 0.0) or 0.0)
            avg_atr_20 = curr_atr
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 5:
                if "atr" in raw_df.columns:
                    s_atr = raw_df["atr"].dropna().astype(float)
                    if len(s_atr) > 0:
                        avg_atr_20 = float(s_atr.iloc[-20:].mean())
                elif "high" in raw_df.columns and "low" in raw_df.columns:
                    ranges = (raw_df["high"].astype(float) - raw_df["low"].astype(float)).dropna()
                    if len(ranges) > 0:
                        avg_atr_20 = float(ranges.iloc[-20:].mean())
            is_market_squeeze = (kc_width_ratio < 0.015) or (avg_atr_20 > 0 and curr_atr < 0.7 * avg_atr_20)

            # 方向總開關（Direction Master Lock）與開倉絕對門檻：
            # 🟢 開多絕對門檻（沒滿足任何一項，強制拒絕開多）：
            # 1. MA3 當前必須明確向上: ma3_curr > ma3_prev
            # 2. MA3 必須位於慢線/中軌上方 (或金叉狀態): ma3_curr >= ma15_curr
            # 3. 當前 K 棒必須是實體陽線: curr_k['close'] > curr_k['open']
            # 只要 MA3 向下、或在慢線下方、或當前收陰，一律一票否決，嚴禁生成任何 LONG 訊號！
            ma15_curr = float(getattr(market_data, "ma15", getattr(market_data, "ema_slow", market_data.kc_middle)))
            curr_close = float(market_data.curr_k.get("close", market_data.current_price))
            curr_open = float(market_data.curr_k.get("open", curr_close))
            kc_mid_val = float(market_data.kc_middle)
            is_ma3_trending_up = ma3_curr > ma3_prev
            is_ma3_above_slow = (ma3_curr >= ma15_curr or ma3_curr >= kc_mid_val or curr_close > kc_mid_val)
            is_candle_bullish = curr_close > curr_open
            hard_long_allowed = is_ma3_trending_up and is_ma3_above_slow and is_candle_bullish

            # 🔴 開空絕對門檻（沒滿足任何一項，強制拒絕開空）：
            # 1. MA3 當前必須明確向下: ma3_curr < ma3_prev
            # 2. MA3 必須位於慢線/中軌下方 (或死叉狀態): ma3_curr <= ma15_curr
            # 3. 當前 K 棒必須是實體陰線: curr_k['close'] < curr_k['open']
            # 只要 MA3 向上、或在慢線上方、或當前收陽，一律一票否決，嚴禁生成任何 SHORT 訊號！
            is_ma3_trending_down = ma3_curr < ma3_prev
            is_ma3_below_slow = (ma3_curr <= ma15_curr or ma3_curr <= kc_mid_val or curr_close < kc_mid_val)
            is_candle_bearish = curr_close < curr_open
            hard_short_allowed = is_ma3_trending_down and is_ma3_below_slow and is_candle_bearish

            if not hard_long_allowed:
                is_breakout_long = False
                is_continuation_long = False
                three_bar_long = False
                is_ma_cross_long = False

            if not hard_short_allowed:
                is_breakdown_short = False
                is_continuation_short = False
                three_bar_short = False
                is_ma_cross_short = False

            from core.gates.direction_master_lock import evaluate_direction_master_lock
            kc_mid_val = float(market_data.kc_middle)
            direction_long_ok, dir_long_reason, _ = evaluate_direction_master_lock(
                "LONG", ma3_curr, ma3_prev, kc_mid_val, ma5_curr,
                ma15_curr=ma15_curr, curr_close=curr_close, curr_open=curr_open
            )
            direction_short_ok, dir_short_reason, _ = evaluate_direction_master_lock(
                "SHORT", ma3_curr, ma3_prev, kc_mid_val, ma5_curr,
                ma15_curr=ma15_curr, curr_close=curr_close, curr_open=curr_open
            )

            # 敏捷進攻架構：妖幣突破訊號與轉折交叉訊號嚴格依循 KC 主趨勢防線與方向總開關
            can_long = hard_long_allowed and direction_long_ok and (not is_macro_bear) and (
                is_breakout_long or is_continuation_long or is_ma_cross_long or (
                    not is_bearish_wave
                    and ma_trend_aligned_long
                    and (ma3_slope >= 0)
                    and (market_data.curr_k["close"] >= ma3_curr)
                    and (market_data.current_price >= ma3_curr)
                )
            )
            can_short = hard_short_allowed and direction_short_ok and (not is_macro_bull) and (
                is_breakdown_short or is_continuation_short or is_ma_cross_short or (
                    not is_bullish_wave
                    and ma_trend_aligned_short
                    and (ma3_slope <= 0)
                    and (market_data.curr_k["close"] <= ma3_curr)
                    and (market_data.current_price <= ma3_curr)
                )
            )

            # ══════════════════════════════════════════════════════════════════
            # 敏捷雙重開倉觸發點（Dual Agile Entry Triggers）
            # 解決滯後問題：破軌即開 / MA3 穿中軌即開，當根收線立即開倉！
            # ══════════════════════════════════════════════════════════════════
            atr_val_now = float(getattr(market_data, "atr", 0.0) or 0.0)
            c_body_now = abs(curr_close - curr_open)
            kc_up_val = float(market_data.kc_upper)
            kc_low_val = float(market_data.kc_lower)

            # 🟢 多頭觸發點 A（MA3 穿中軌即開）：
            agile_cross_long = (
                curr_close > kc_mid_val
                and (ma3_curr >= ma15_curr or ma3_curr >= kc_mid_val)
                and ma3_curr > ma3_prev
                and curr_close > curr_open
            )
            # 🟢 多頭觸發點 B（實體衝破 KC 上軌即開）：
            agile_breakout_long = (
                curr_close > kc_up_val
                and (c_body_now >= 0.40 * atr_val_now or curr_close > kc_up_val)
                and curr_close > curr_open
                and ma3_curr > ma3_prev
            )

            # 🔴 空頭觸發點 A（MA3 跌破中軌即開）：
            agile_cross_short = (
                curr_close < kc_mid_val
                and (ma3_curr <= ma15_curr or ma3_curr <= kc_mid_val)
                and ma3_curr < ma3_prev
                and curr_close < curr_open
            )
            # 🔴 空頭觸發點 B（實體跌破 KC 下軌即開）：
            agile_breakdown_short = (
                curr_close < kc_low_val
                and (c_body_now >= 0.40 * atr_val_now or curr_close < kc_low_val)
                and curr_close < curr_open
                and ma3_curr < ma3_prev
            )

            # ══════════════════════════════════════════════════════════════════
            # 🚫 假突破防範 (KC Squeeze / Fakeout Filter - Anti-Bull-Trap)
            # 當 KC 中軌走平或往下時，若價格僅是短期衝出 KC 上軌但缺乏放量強實體或上影線過長，
            # 判定為假突破誘多（Bull Trap），一票否決開多，防止買在假突破頂部！
            # ══════════════════════════════════════════════════════════════════
            upper_wick_now = float(market_data.curr_k.get("high", curr_close)) - max(curr_close, curr_open)
            is_bull_trap = bool(
                kc_slope <= 0.000005
                and curr_close >= (kc_up_val - 0.2 * atr_val_now)
                and (c_body_now < 0.6 * atr_val_now or upper_wick_now >= 0.6 * c_body_now or curr_close <= curr_open)
            )
            if is_bull_trap:
                can_long = False
                is_breakout_long = False
                is_continuation_long = False
                agile_breakout_long = False
                logger.info(
                    f"🚫 [FAKE_BREAKOUT_BULL_TRAP] {symbol} KC中軌走平/向下 (slope={kc_slope:.6f})，"
                    f"上軌附近缺乏放量實體或上影過長 (body={c_body_now:.6f}, wick={upper_wick_now:.6f}, atr={atr_val_now:.6f})，"
                    f"判定為假突破誘多 (Bull Trap)，一票否決開多！"
                )

            lower_wick_now = min(curr_close, curr_open) - float(market_data.curr_k.get("low", curr_close))
            is_bear_trap = bool(
                kc_slope >= -0.000005
                and curr_close <= (kc_low_val + 0.2 * atr_val_now)
                and (c_body_now < 0.6 * atr_val_now or lower_wick_now >= 0.6 * c_body_now or curr_close >= curr_open)
            )
            if is_bear_trap:
                can_short = False
                is_breakdown_short = False
                is_continuation_short = False
                agile_breakdown_short = False
                logger.info(
                    f"🚫 [FAKE_BREAKDOWN_BEAR_TRAP] {symbol} KC中軌走平/向上 (slope={kc_slope:.6f})，"
                    f"下軌附近缺乏放量實體或下影過長 (body={c_body_now:.6f}, wick={lower_wick_now:.6f}, atr={atr_val_now:.6f})，"
                    f"判定為假跌破誘空 (Bear Trap)，一票否決開空！"
                )

            is_agile_trigger = False
            agile_signal = None
            if agile_breakout_long:
                is_agile_trigger = True
                agile_signal = "ENTRY_LONG_BREAKOUT"
                can_long = True
            elif agile_cross_long:
                is_agile_trigger = True
                agile_signal = "ENTRY_LONG_CROSS_MID"
                can_long = True
            elif agile_breakdown_short:
                is_agile_trigger = True
                agile_signal = "ENTRY_SHORT_BREAKDOWN"
                can_short = True
            elif agile_cross_short:
                is_agile_trigger = True
                agile_signal = "ENTRY_SHORT_CROSS_MID"
                can_short = True

            # 任務 2：【特例放行】谷底與頂部轉折提前開倉（雙向豁免 KC 斜率）
            from core.services.swing_pivot_rules import evaluate_swing_pivot_entry
            raw_swing_pivot = evaluate_swing_pivot_entry(raw_df)
            swing_pivot = None
            if raw_swing_pivot:
                pivot_side = raw_swing_pivot.get("side")
                # 轉折開多/開空也必須嚴格遵守 MA3 斜率、慢線位置與 K 棒顏色硬性門檻
                if pivot_side == "LONG":
                    if not hard_long_allowed:
                        logger.warning(
                            f"🛑 [HARD_VETO] {symbol} SWING_PIVOT_LONG 違反開多絕對門檻 "
                            f"(ma3_up={is_ma3_trending_up}, ma3>=slow={is_ma3_above_slow}, bullish={is_candle_bullish})，一票否決！"
                        )
                    else:
                        swing_pivot = raw_swing_pivot
                elif pivot_side == "SHORT":
                    if not hard_short_allowed:
                        logger.warning(
                            f"🛑 [HARD_VETO] {symbol} SWING_PIVOT_SHORT 違反開空絕對門檻 "
                            f"(ma3_down={is_ma3_trending_down}, ma3<=slow={is_ma3_below_slow}, bearish={is_candle_bearish})，一票否決！"
                        )
                    else:
                        swing_pivot = raw_swing_pivot

            is_swing_pivot = bool(swing_pivot)
            if is_swing_pivot:
                candidate_side = swing_pivot["side"]
                if candidate_side == "LONG" and hard_long_allowed:
                    can_long = True
                elif candidate_side == "SHORT" and hard_short_allowed:
                    can_short = True

            # 強動能破軌或外軌延續開倉判定 (外軌鎖定強制上車，豁免平倉後冷卻期)
            c_body = abs(market_data.curr_k["close"] - market_data.curr_k["open"])
            is_strong_breakout = (
                ((is_breakout_long or is_continuation_long) and (c_body >= 0.35 * market_data.atr or curr_quote > market_data.kc_upper))
                or ((is_breakdown_short or is_continuation_short) and (c_body >= 0.35 * market_data.atr or curr_quote < market_data.kc_lower))
            )

            # 1. 平倉後強制冷卻檢查 (強動能破軌、敏捷觸發與反手開倉放行，SWING_PIVOT 嚴格遵守冷卻防止反覆刷單)
            acc_key = cls._get_account_key(account, symbol)
            last_exit = cls._last_exit_record.get(acc_key)
            target_side = "LONG" if can_long else ("SHORT" if can_short else "")
            is_reverse_position = bool(last_exit and last_exit.get("side") and last_exit.get("side") != target_side)
            if last_exit and not is_strong_breakout and not is_agile_trigger and not is_reverse_position:
                elapsed_sec = time.time() - float(last_exit.get("timestamp", 0.0))
                if elapsed_sec < 180:
                    logger.debug(f"⏳ [COOLDOWN] {symbol} 平倉後強制冷卻中: 剩餘 {180 - elapsed_sec:.1f} 秒 (不足 3 根 1m K 棒)，禁止開倉")
                    return {"status": "VETO", "symbol": symbol, "reason": "VETO_EXIT_COOLDOWN"}

            # 2. 單一合約 1 小時內開倉次數上限 (3～4 次，預設 3 次)
            now_ts = time.time()
            entry_history = [t for t in cls._hourly_entry_records.get(acc_key, cls._hourly_entry_records.get(symbol, [])) if (now_ts - t) < 3600.0]
            cls._hourly_entry_records[acc_key] = entry_history
            cls._hourly_entry_records[symbol] = entry_history
            if len(entry_history) >= cls.MAX_ENTRIES_PER_HOUR:
                logger.info(f"🚫 [RATE_LIMIT] {symbol} 1 小時內已開倉 {len(entry_history)} 次，達到上限 ({cls.MAX_ENTRIES_PER_HOUR} 次)，禁止開倉！")
                return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED"}

            if is_agile_trigger and agile_signal:
                candidate_signal = agile_signal
            elif can_long and (is_breakout_long or is_continuation_long):
                candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if (p_close > p_up or is_continuation_long) else "BREAKOUT_LONG"
            elif can_short and (is_breakdown_short or is_continuation_short):
                candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if (p_close < p_low or is_continuation_short) else "BREAKDOWN_SHORT"
            elif is_swing_pivot and ((swing_pivot['side'] == "LONG" and can_long) or (swing_pivot['side'] == "SHORT" and can_short)):
                candidate_signal = f"SWING_PIVOT_{swing_pivot['side']}"
            elif can_long:
                if is_ma_cross_long:
                    candidate_signal = "ENTRY_LONG_MA3_MA15_CROSS_MID"
                elif is_pullback_long:
                    candidate_signal = "TREND_PULLBACK_LONG"
                elif (market_data.curr_k["close"] > market_data.ema_fast and market_data.ema_fast >= market_data.ema_slow and market_data.kc_middle_slope >= 0 and ma5_curr > ma5_prev):
                    if market_data.curr_k["close"] > market_data.kc_upper:
                        candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if p_close > p_up else "BREAKOUT_LONG"
                    else:
                        candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"

            elif can_short:
                if is_ma_cross_short:
                    candidate_signal = "ENTRY_SHORT_MA3_MA15_CROSS_MID"
                elif is_pullback_short:
                    candidate_signal = "TREND_PULLBACK_SHORT"
                elif (market_data.curr_k["close"] < market_data.ema_fast and market_data.ema_fast <= market_data.ema_slow and market_data.kc_middle_slope <= 0 and ma5_curr < ma5_prev):
                    if market_data.curr_k["close"] < market_data.kc_lower:
                        candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if p_close < p_low else "BREAKDOWN_SHORT"
                    else:
                        candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"

            if candidate_signal:
                # Zero-trust whitelist: ordinary candidates need the closed-bar
                # MA3/MA15 cross; breakout and pivot routes use their own
                # closed-bar structural confirmation.
                from core.services.strict_ma3_ma15_entry_gate import evaluate_ma3_ma15_entry

                candidate_side = "LONG" if "LONG" in candidate_signal else "SHORT"
                if candidate_side == "LONG":
                    if not hard_long_allowed:
                        logger.warning(
                            f"🛑 [HARD_LONG_VETO] {symbol} {candidate_signal} 違反開多絕對門檻 "
                            f"(ma3_up={is_ma3_trending_up}, ma3>=slow={is_ma3_above_slow}, bullish={is_candle_bullish})，一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_HARD_LONG_MA3_OR_CANDLE"}
                    if not direction_long_ok and not (is_swing_pivot or three_bar_long or is_agile_trigger):
                        logger.warning(
                            f"🛑 [DIRECTION_MASTER_LOCK] {symbol} {candidate_signal} 違反開多方向鎖 "
                            f"(ma3={ma3_curr:.6f}, kc_mid={kc_mid_val:.6f}, ma3_prev={ma3_prev:.6f})，一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_DIRECTION_MASTER_LOCK_LONG"}
                elif candidate_side == "SHORT":
                    if not hard_short_allowed:
                        logger.warning(
                            f"🛑 [HARD_SHORT_VETO] {symbol} {candidate_signal} 違反開空絕對門檻 "
                            f"(ma3_down={is_ma3_trending_down}, ma3<=slow={is_ma3_below_slow}, bearish={is_candle_bearish})，一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_HARD_SHORT_MA3_OR_CANDLE"}
                    if not direction_short_ok and not (is_swing_pivot or three_bar_short or is_agile_trigger):
                        logger.warning(
                            f"🛑 [DIRECTION_MASTER_LOCK] {symbol} {candidate_signal} 違反開空方向鎖 "
                            f"(ma3={ma3_curr:.6f}, kc_mid={kc_mid_val:.6f}, ma3_prev={ma3_prev:.6f})，一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_DIRECTION_MASTER_LOCK_SHORT"}

                entry_frame = getattr(market_data, "raw_df", None)
                account_gate_state = getattr(account, "entry_filter_last", None)
                if not isinstance(account_gate_state, dict):
                    account_gate_state = {}
                    try:
                        account.entry_filter_last = account_gate_state
                    except (AttributeError, TypeError):
                        pass
                persisted_trade_bars = account_gate_state.get(
                    "strict_ma3_ma15_last_trade_bar", {}
                )
                if not isinstance(persisted_trade_bars, dict):
                    persisted_trade_bars = {}
                atr_for_stop = float((swing_pivot or {}).get("atr") or getattr(market_data, "atr", 0.0) or 0.0)
                execution_price = float(getattr(market_data, "current_price", 0.0) or 0.0)
                if is_swing_pivot:
                    stop_loss = float(swing_pivot["stop_loss_price"])
                elif three_bar_stop is not None:
                    stop_loss = float(three_bar_stop)
                else:
                    stop_loss = (
                        execution_price - 3.0 * atr_for_stop if candidate_side == "LONG"
                        else execution_price + 3.0 * atr_for_stop
                    )
                if is_agile_trigger:
                    from types import SimpleNamespace
                    from core.services.candle_data import closed_entry_candles
                    closed_agile_bars = closed_entry_candles(entry_frame) if entry_frame is not None else None
                    agile_bar_time = int(float(closed_agile_bars.iloc[-1]["timestamp"])) if (closed_agile_bars is not None and len(closed_agile_bars)) else int(time.time() * 1000)
                    last_used = persisted_trade_bars.get(symbol) or cls._last_trade_bar_time.get(symbol)
                    valid_stop = stop_loss < execution_price if candidate_side == "LONG" else stop_loss > execution_price
                    if not valid_stop:
                        stop_loss = execution_price - 2.5 * atr_for_stop if candidate_side == "LONG" else execution_price + 2.5 * atr_for_stop
                    if last_used and last_used == agile_bar_time:
                        return {"status": "VETO", "symbol": symbol, "reason": "AGILE_TRIGGER_BAR_ALREADY_TRADED"}
                    whitelist = SimpleNamespace(
                        action=f"OPEN_{candidate_side}", bar_time=agile_bar_time,
                        entry_price=execution_price, stop_loss_price=stop_loss,
                        checklist={"AGILE_TRIGGER_AUTHORIZED": True, "STOP_VALID": True},
                        violations=[],
                    )
                elif is_swing_pivot:
                    from types import SimpleNamespace
                    from core.services.candle_data import closed_entry_candles
                    closed_pivot_bars = closed_entry_candles(entry_frame)
                    pivot_bar_time = int(float(closed_pivot_bars.iloc[-1]["timestamp"])) if len(closed_pivot_bars) else None
                    last_used = persisted_trade_bars.get(symbol) or cls._last_trade_bar_time.get(symbol)
                    valid_stop = stop_loss < execution_price if candidate_side == "LONG" else stop_loss > execution_price
                    if (not pivot_bar_time or last_used == pivot_bar_time or not valid_stop):
                        return {"status": "VETO", "symbol": symbol, "reason": "SWING_PIVOT_BAR_OR_STOP_INVALID"}
                    whitelist = SimpleNamespace(
                        action=f"OPEN_{candidate_side}", bar_time=pivot_bar_time,
                        entry_price=execution_price, stop_loss_price=stop_loss,
                        checklist={"CLOSED_SWING_PIVOT": True, "PIVOT_STOP_VALID": True},
                        violations=[],
                    )
                else:
                    from core.services.candle_data import closed_entry_candles
                    whitelist_frame = closed_entry_candles(entry_frame)
                    is_breakout_or_continuation = (
                        (is_breakout_long or is_continuation_long) if candidate_side == "LONG"
                        else (is_breakdown_short or is_continuation_short)
                    )
                    whitelist = evaluate_ma3_ma15_entry(
                        whitelist_frame,
                        candidate_side,
                        current_position="IDLE" if not has_position else "OPEN",
                        last_trade_bar_time=(
                            persisted_trade_bars.get(symbol)
                            or cls._last_trade_bar_time.get(symbol)
                        ),
                        stop_loss_price=stop_loss,
                        execution_price=execution_price,
                        confirmed_breakout=is_breakout_or_continuation,
                    )
                if whitelist.action not in ("OPEN_LONG", "OPEN_SHORT"):
                    reason = whitelist.violations[0] if whitelist.violations else "ZERO_TRUST_WHITELIST_REJECTED"
                    logger.info(
                        "[ZERO_TRUST_ENTRY_VETO] %s side=%s reason=%s checks=%s",
                        symbol, candidate_side, reason, whitelist.checklist,
                    )
                    return {
                        "status": "VETO", "action": whitelist.action, "symbol": symbol,
                        "reason": reason, "checklist": whitelist.checklist,
                        "violations": whitelist.violations,
                    }

                last_exit_context = {
                    **(last_exit or {}),
                    "hourly_entry_count": len(entry_history),
                    "current_time": now_ts,
                }
                if is_agile_trigger:
                    should_entry, reason, meta = True, f"AGILE_TRIGGER_{candidate_signal}", {
                        "side": candidate_side,
                        "direction_override": True,
                        "entry_type": "CROSS_MID" if "CROSS_MID" in candidate_signal else "BREAKOUT",
                        "stop_loss_price": stop_loss,
                        "is_cross_mid": "CROSS_MID" in candidate_signal,
                        "allow_kc_internal": True,
                        "strategy_mode": "AGILE_MOMENTUM",
                    }
                elif is_swing_pivot:
                    should_entry, reason, meta = True, "CLOSED_SWING_PIVOT_OVERRIDE_ALIGNED", {
                        "side": candidate_side,
                        "direction_override": True,
                        "entry_type": "SWING_PIVOT",
                        "stop_loss_price": stop_loss,
                    }
                elif three_bar_long or three_bar_short:
                    should_entry, reason, meta = True, f"THREE_BAR_CONTINUATION_{candidate_side}", {
                        "side": candidate_side,
                        "direction_override": True,
                        "entry_type": "CONTINUATION",
                        "stop_loss_price": stop_loss,
                    }
                else:
                    should_entry, reason, meta = EntryGate.evaluate(
                        signal_type=candidate_signal,
                        market_data=market_data,
                        last_exit_context=last_exit_context,
                    )
                if should_entry:
                    side = meta.get("side", "LONG" if "LONG" in candidate_signal else "SHORT")
                    # Claim synchronously before the first await in execution.
                    # A failed/uncertain attempt stays consumed for this bar;
                    # reconciliation must happen before a retry on a later bar.
                    cls._last_trade_bar_time[symbol] = whitelist.bar_time
                    persisted_trade_bars[symbol] = whitelist.bar_time
                    account_gate_state["strict_ma3_ma15_last_trade_bar"] = persisted_trade_bars
                    save_state = getattr(account, "save_state", None)
                    if not callable(save_state):
                        return {
                            "status": "VETO", "action": whitelist.action, "symbol": symbol,
                            "reason": "BAR_CLAIM_STORE_UNAVAILABLE",
                            "checklist": whitelist.checklist,
                        }
                    try:
                        try:
                            save_state(strict=True)
                        except TypeError:
                            save_state()
                    except Exception as exc:
                        logger.error(
                            "[ZERO_TRUST_ENTRY_VETO] %s unable to persist bar claim: %s",
                            symbol, exc,
                        )
                        return {
                            "status": "VETO", "symbol": symbol,
                            "action": whitelist.action,
                            "reason": "BAR_CLAIM_PERSISTENCE_FAILED",
                            "checklist": whitelist.checklist,
                        }
                    logger.info(f"🎯 [ENTRY_TRIGGERED] {symbol} {side} {reason} | meta={meta}")
                    print(f"🎯 [ENTRY_TRIGGERED] {symbol} {side} {reason}", flush=True)
                    call_meta = dict(meta or {})
                    call_meta["candidate_signal_type"] = candidate_signal
                    call_meta["whitelist_entry_price"] = whitelist.entry_price
                    call_meta["stop_loss_price"] = whitelist.stop_loss_price
                    call_meta["whitelist_checklist"] = whitelist.checklist
                    call_meta["atr"] = atr_for_stop
                    call_meta["macro_trend"] = market_data.macro_trend
                    if is_swing_pivot:
                        call_meta.update(
                            entry_type="SWING_PIVOT",
                            direction_override=True,
                            stop_loss_price=stop_loss,
                            source="UNIFIED_PIPELINE|SWING_PIVOT",
                            swing_pivot=swing_pivot,
                        )
                    elif is_agile_trigger:
                        call_meta.update(
                            entry_type="CROSS_MID" if "CROSS_MID" in candidate_signal else "BREAKOUT",
                            direction_override=True,
                            is_agile_trigger=True,
                            is_cross_mid="CROSS_MID" in candidate_signal,
                            allow_kc_internal=True,
                            strategy_mode="AGILE_MOMENTUM",
                            stop_loss_price=stop_loss,
                            source=f"UNIFIED_PIPELINE|{candidate_signal}",
                        )
                    elif three_bar_long or three_bar_short:
                        call_meta.update(
                            entry_type="CONTINUATION",
                            direction_override=True,
                            stop_loss_price=stop_loss,
                            source=f"UNIFIED_PIPELINE|THREE_BAR_CONTINUATION_{candidate_side}",
                        )
                    call_meta["upstream_entry_gate_authorized"] = True
                    try:
                        call_meta["intrabar_authorized"] = bool(
                            raw_df is not None
                            and not bool(raw_df.iloc[-1].get("is_closed", True))
                        )
                    except (AttributeError, IndexError, TypeError):
                        call_meta["intrabar_authorized"] = False
                    if any(k in candidate_signal for k in ("CROSS_MID", "MA_CROSS", "MID_LINE", "PULLBACK", "AGILE")):
                        call_meta["is_cross_mid"] = True
                        call_meta["allow_kc_internal"] = True
                        call_meta["direction_override"] = True
                        call_meta["strategy_mode"] = "AGILE_MOMENTUM"
                        call_meta["entry_type"] = "CROSS_MID"
                    if "raw_df" not in call_meta or call_meta["raw_df"] is None:
                        call_meta["raw_df"] = getattr(market_data, "raw_df", None)
                    if call_meta.get("raw_df") is None and klines_df is not None:
                        call_meta["raw_df"] = klines_df
                    raw_df_instance = call_meta.get("raw_df")
                    if hasattr(raw_df_instance, "attrs"):
                        for k in ("direction_override", "is_agile_trigger", "is_cross_mid", "entry_type", "strategy_mode", "allow_kc_internal"):
                            if k in call_meta:
                                raw_df_instance.attrs[k] = call_meta[k]
                    success = await cls.open_position(
                        engine_or_account=engine_or_account,
                        symbol=symbol,
                        side=side,
                        price=market_data.current_price,
                        reason=f"EntryGate:{reason}",
                        meta=call_meta,
                    )
                    return {
                        "status": "ENTRY_EXECUTED" if success else "VETO",
                        "symbol": symbol,
                        "action": whitelist.action if success else "HOLD / PASS",
                        "side": side, "reason": reason, "success": success,
                        "checklist": whitelist.checklist,
                        "entry_price": whitelist.entry_price,
                        "stop_loss_price": whitelist.stop_loss_price,
                    }
                else:
                    logger.debug(f"🚫 [VETO] {symbol} {reason}")
                    return {"status": "VETO", "symbol": symbol, "reason": reason}

        return {"status": "IDLE", "symbol": symbol}

    _last_exit_record: Dict[str, Dict[str, Any]] = {}
    _last_trade_bar_time: Dict[str, int] = {}
    _hourly_entry_records: Dict[str, list] = {}
    MAX_ENTRIES_PER_HOUR: int = 3

    @classmethod
    def _get_account_key(cls, account: Any, symbol: str) -> str:
        acc_id = getattr(account, "account_id", None) or getattr(account, "id", None) or id(account)
        return f"{acc_id}:{symbol}"

    @classmethod
    async def close_position(
        cls,
        account: Any,
        symbol: str,
        price: float,
        reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """全系統唯一平倉執行管道"""
        try:
            if hasattr(account, "close_position"):
                closed_side = None
                positions_dict = getattr(account, "positions", {})
                if isinstance(positions_dict, dict) and symbol in positions_dict:
                    closed_side = positions_dict[symbol].get("side")

                raw_df = (meta or {}).get("raw_df")
                if raw_df is not None:
                    if not hasattr(account, "_channel_exit_frames"):
                        account._channel_exit_frames = {}
                    account._channel_exit_frames[symbol] = raw_df
                is_emergency = any(token in str(reason).upper() for token in (
                    "OPPOSITE_KC_BAND_BREACH", "EMERGENCY", "HARD_STOP", "CIRCUIT_BREAKER",
                    "WATERFALL", "CATASTROPHIC_DUMP", "CATASTROPHIC_PUMP", "閃崩", "瀑布"
                    , "EXIT_LONG_AT_PEAK", "EXIT_SHORT_AT_BOTTOM", "TWO_CLOSED_ADVERSE_ABNORMAL"
                ))
                is_limit = False if is_emergency else bool((meta or {}).get("is_limit", False))
                success = await account.close_position(
                    symbol=symbol,
                    current_price=price,
                    close_reason=reason,
                    is_limit=is_limit,
                )
                if success and closed_side:
                    acc_key = cls._get_account_key(account, symbol)
                    record = {
                        "side": closed_side,
                        "timestamp": time.time(),
                        "price": price,
                        "exit_price": price,
                        "exit_high": float((meta or {}).get("pivot_meta", {}).get("peak_high") or (meta or {}).get("highest_price") or price),
                        "exit_low": float((meta or {}).get("pivot_meta", {}).get("valley_low") or (meta or {}).get("lowest_price") or price),
                        "reason": reason,
                    }
                    cls._last_exit_record[acc_key] = record
                    cls._last_exit_record[symbol] = record
                    if hasattr(account, "position_meta") and isinstance(account.position_meta, dict):
                        pos_meta = account.position_meta.get(symbol, {})
                        if isinstance(pos_meta, dict):
                            pos_meta.pop("three_tier_net_roe_lock_state", None)
                            pos_meta.pop("highest_net_roe", None)
                            pos_meta.pop("peak_net_roe_pct", None)
                            pos_meta.pop("profit_lock_armed", None)
                            pos_meta.pop("peak_roe", None)
                    # 徹底撤銷交易所端殘留的所有條件委託單，防範孤兒止損單
                    try:
                        if hasattr(account, "_cancel_all_orders"):
                            await account._cancel_all_orders(symbol)
                    except Exception as cancel_exc:
                        logger.warning(f"⚠️ [EXECUTION] 平倉後撤單警告 {symbol}: {cancel_exc}")
                return success
            return False
        except Exception as exc:
            logger.error(f"[EXECUTION] close_position 異常 {symbol}: {exc}")
            return False

    @classmethod
    async def open_position(
        cls,
        engine_or_account: Any,
        symbol: str,
        side: str,
        price: float,
        reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """全系統唯一開倉執行管道"""
        try:
            account = getattr(engine_or_account, "account", engine_or_account)
            # 檢查持倉與鎖定
            if symbol in getattr(account, "positions", {}):
                logger.info(f"[EXECUTION] {symbol} 已有持倉，跳過開倉")
                return False
            if symbol in getattr(account, "closing_lock", set()):
                return False

            # 計算保證金與槓桿
            wallet = float(getattr(account, "get_wallet_balance", lambda: 100.0)())
            available = float(getattr(account, "get_available_balance", lambda: wallet)())

            engine = engine_or_account if hasattr(engine_or_account, "account") else None
            leverage = 5
            if engine and hasattr(engine, "symbol_rotation"):
                try:
                    leverage = engine.symbol_rotation.get_dynamic_leverage(symbol, 100)
                except Exception:
                    leverage = 5

            amount_usdt = 0.0
            fee_buffer = 1.0 + leverage * 0.0005
            fee_safe_available = (available / fee_buffer) * 0.98
            if engine and hasattr(engine, "_full_wallet_entry_margin"):
                try:
                    amount_usdt = engine._full_wallet_entry_margin(wallet, available, leverage, symbol)
                except Exception:
                    amount_usdt = fee_safe_available
            else:
                amount_usdt = fee_safe_available

            # 依據 stepSize 進行向下截斷（Floor Truncate），嚴防超額保證金或 LOT_SIZE 拒單
            step_size = float((meta or {}).get("step_size") or (meta or {}).get("stepSize") or 0.0)
            if step_size > 0 and price > 0 and leverage > 0:
                raw_qty = (amount_usdt * leverage) / price
                truncated_qty = math.floor(raw_qty / step_size) * step_size
                if (truncated_qty * price) < 5.0:
                    logger.warning(
                        f"[EXECUTION] {symbol} 名義價值不足 5 USDT: {(truncated_qty * price):.2f}U "
                        f"(qty={truncated_qty}, price={price}, step_size={step_size})"
                    )
                    return False
                if truncated_qty > 0:
                    amount_usdt = (truncated_qty * price) / leverage

            if amount_usdt < 5.0:
                logger.warning(f"[EXECUTION] {symbol} 可用保證金不足: {amount_usdt:.2f}U (available={available:.2f}U)")
                return False

            frame = (meta or {}).get("raw_df")
            if frame is None and engine is not None and hasattr(engine, "_channel_exit_frames"):
                frame = engine._channel_exit_frames.get(symbol)
            if frame is None and hasattr(account, "entry_frame_provider"):
                provider = getattr(account, "entry_frame_provider", None)
                if callable(provider):
                    try:
                        frame = provider(symbol)
                    except Exception:
                        frame = None

            if frame is not None:
                if not hasattr(frame, "attrs"):
                    try:
                        frame.attrs = {}
                    except Exception:
                        pass
                if hasattr(frame, "attrs"):
                    frame.attrs["entry_finality_verified"] = True
                    frame.attrs.setdefault("timeframe_ms", 60_000)
                    macro_label = (meta or {}).get("macro_trend")
                    if macro_label:
                        frame.attrs["macro_trend"] = str(macro_label).upper()

            atr = float((meta or {}).get("atr") or (price * 0.015))
            requested_sl = (meta or {}).get("stop_loss_price")
            if requested_sl is not None:
                requested_sl = float(requested_sl)
                if (not math.isfinite(requested_sl)
                        or (str(side).upper() == "LONG" and requested_sl >= price)
                        or (str(side).upper() == "SHORT" and requested_sl <= price)):
                    logger.warning("[EXECUTION] %s invalid whitelist stop; fail closed", symbol)
                    return False
            if str(side).upper() == "LONG":
                initial_sl = requested_sl if requested_sl is not None else max(0.0, price - 3.0 * atr)
            else:
                initial_sl = requested_sl if requested_sl is not None else price + 3.0 * atr

            bar_id = None
            if frame is not None and len(frame) > 0:
                try:
                    bar_id = float(frame.iloc[-1].get("timestamp", frame.iloc[-1].get("time", 0.0)))
                except (ValueError, TypeError):
                    bar_id = None
            if bar_id is None:
                bar_id = float((meta or {}).get("bar_timestamp") or time.time())

            confirmation = (
                "MID_LINE_BREAKDOWN" if "MID" in str(reason).upper() and side == "SHORT"
                else "MID_LINE_BREAKOUT" if "MID" in str(reason).upper() and side == "LONG"
                else (meta or {}).get("structure_confirmation") or (meta or {}).get("entry_pattern") or "BREAKOUT"
            )

            from core.gates.central_entry_gate import CentralEntryGate
            signal_id = (meta or {}).get("signal_id") or CentralEntryGate.generate_signal_id(symbol, side, "UNIFIED_PIPELINE")
            is_cross_mid = bool(
                (meta or {}).get("is_cross_mid")
                or (meta or {}).get("allow_kc_internal")
                or (meta or {}).get("strategy_mode") == "AGILE_MOMENTUM"
                or (meta or {}).get("entry_type") == "SWING_PIVOT"
                or any(k in str(reason).upper() for k in ("CROSS_MID", "MA_CROSS", "EMA_CROSS", "MID_LINE", "PULLBACK", "AGILE"))
                or any(k in str(confirmation).upper() for k in ("MID_LINE", "CROSS", "AGILE"))
            )

            entry_context = {
                "is_manual": True,
                "managed_by_bot": True,
                "entry_mode": "WILD_SWING",
                "strategy_mode": "AGILE_MOMENTUM" if is_cross_mid else "CONSERVATIVE",
                "source": "UNIFIED_PIPELINE",
                "candidate_signal_type": (meta or {}).get("candidate_signal_type"),
                "upstream_entry_gate_authorized": bool(
                    (meta or {}).get("upstream_entry_gate_authorized")
                ),
                "intrabar_authorized": bool((meta or {}).get("intrabar_authorized")),
                "entry_pattern": confirmation,
                "structure_confirmation": confirmation,
                "is_outer_entry": not is_cross_mid,
                "allow_kc_internal": is_cross_mid,
                "direction_override": is_cross_mid,
                "entry_type": (
                    "SWING_PIVOT" if (meta or {}).get("entry_type") == "SWING_PIVOT"
                    else "CROSS_MID" if is_cross_mid else "BREAKOUT"
                ),
                "initial_sl": initial_sl,
                "stop_loss_price": initial_sl,
                "signal_id": signal_id,
                "channel_confirmation_bar_id": bar_id,
                "bar_id": bar_id,
                "dual_tf_meta": {"bar_id": bar_id},
                "is_intrabar": bool((meta or {}).get("intrabar_authorized")),
                "meta": meta or {},
            }
            if frame is not None:
                if hasattr(frame, "attrs"):
                    frame.attrs.setdefault("timeframe_ms", 60_000.0)
                entry_context["entry_gate_frame"] = frame
                entry_context["frame"] = frame
                entry_context["raw_df"] = frame

            if hasattr(account, "open_position"):
                opened = await account.open_position(
                    symbol=symbol,
                    side=side,
                    price=price,
                    amount_usdt=amount_usdt,
                    sl=initial_sl,
                    tp=0.0,
                    reason=reason,
                    atr=atr,
                    leverage=leverage,
                    signal_score=100,
                    entry_context=entry_context,
                )
                if opened:
                    cls._hourly_entry_records.setdefault(symbol, []).append(time.time())
                    if hasattr(account, "position_meta") and isinstance(account.position_meta, dict):
                        pos_meta = account.position_meta.get(symbol, {})
                        if isinstance(pos_meta, dict):
                            pos_meta.pop("three_tier_net_roe_lock_state", None)
                            pos_meta.pop("highest_net_roe", None)
                            pos_meta.pop("peak_net_roe_pct", None)
                            pos_meta.pop("profit_lock_armed", None)
                            pos_meta.pop("peak_roe", None)
                    if hasattr(account, "positions") and isinstance(account.positions, dict) and symbol in account.positions:
                        pos = account.positions[symbol]
                        if isinstance(pos, dict):
                            pos.pop("three_tier_net_roe_lock_state", None)
                            pos.pop("highest_net_roe", None)
                            pos.pop("peak_net_roe_pct", None)
                            pos.pop("profit_lock_armed", None)
                            pos.pop("peak_roe", None)
                    logger.info(f"🎉 [UNIFIED_ORDER_FILLED] {symbol} {side} @ {price} | Margin={amount_usdt:.2f}U Lev={leverage}x")
                    print(f"🎉 [UNIFIED_ORDER_FILLED] {symbol} {side} @ {price} | Margin={amount_usdt:.2f}U Lev={leverage}x", flush=True)
                return opened
            return False
        except Exception as exc:
            logger.error(f"[EXECUTION] open_position 異常 {symbol}: {exc}", exc_info=True)
            return False
