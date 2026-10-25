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

        # 2. 若有持倉：只問 ExitGate (唯一裁決官)
        if has_position:
            should_exit, reason, meta = ExitGate.evaluate(
                pos=current_position,
                market_data=market_data,
            )
            if should_exit:
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
                return {"status": "EXIT_EXECUTED", "symbol": symbol, "reason": reason, "success": success}
            else:
                logger.debug(f"🛡️ [HOLD] {symbol} {reason}")
                return {"status": "HOLD", "symbol": symbol, "reason": reason}

        # 3. 若無持倉：只問 EntryGate (唯一裁決官)
        else:
            # 偵測候選方向訊號 (大週期順勢原則：多頭大週期只看多、空頭大週期只看空，絕不頻繁多空互反)
            candidate_signal = None
            ma5_curr = float(market_data.curr_k.get("ma5", market_data.current_price))
            ma5_prev = float(market_data.prev_k.get("ma5", ma5_curr))
            ma15_curr = float(market_data.curr_k.get("ma15", getattr(market_data, "ma15", ma5_curr)))
            ma15_prev = float(market_data.prev_k.get("ma15", ma15_curr))
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
                if len(closed_ma) >= 2 and {"ma3", "ma15"}.issubset(closed_ma.columns):
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

            # 破軌衝擊信號（收線突破外軌，通道走平或向上即刻發動，杜絕盤中毛刺）：
            is_breakout_long = (
                market_data.curr_k["close"] > market_data.kc_upper
                and market_data.kc_middle_slope >= -0.000005
                and ma5_curr > ma5_prev
                and ma3_directional_long
            )
            is_breakdown_short = (
                market_data.curr_k["close"] < market_data.kc_lower
                and market_data.kc_middle_slope <= 0.000005
                and ma5_curr < ma5_prev
                and ma3_directional_short
            )

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

            # MA3 / MA5 交叉中軌敏捷轉折開倉 (AGILE_MOMENTUM)：
            # 做多：MA3 或 MA5 向上金叉/發散於 MA15，斜率向上，KC 中軌走平或微揚，K 棒實體陽線且站穩中軌之上
            ma3_curr = float(market_data.curr_k.get("ma3", getattr(market_data, "ma3", ma5_curr)))
            ma3_prev = float(market_data.prev_k.get("ma3", getattr(market_data, "prev_ma3", ma3_curr)))
            ma3_slope = ma3_curr - ma3_prev

            is_ma_cross_long = live_cross_long is not None
            # 做空：MA3 或 MA5 向下死叉/發散於 MA15，斜率向下，KC 中軌走平或微垂，K 棒實體陰線且壓制在中軌之下
            is_ma_cross_short = live_cross_short is not None

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

            # 敏捷進攻架構：妖幣突破訊號與轉折交叉訊號豁免大週期波段鎖定；常規追單仍依循順勢波段
            can_long = is_breakout_long or is_ma_cross_long or (
                not is_bearish_wave
                and ma_trend_aligned_long
                and (ma3_slope >= 0)
                and (market_data.curr_k["close"] >= ma3_curr)
                and (market_data.current_price >= ma3_curr)
            )
            can_short = is_breakdown_short or is_ma_cross_short or (
                not is_bullish_wave
                and ma_trend_aligned_short
                and (ma3_slope <= 0)
                and (market_data.curr_k["close"] <= ma3_curr)
                and (market_data.current_price <= ma3_curr)
            )

            # 強動能破軌判定 (實體大陽/大陰且收在軌外，交由 EntryGate 判定豁免冷卻期)
            c_body = abs(market_data.curr_k["close"] - market_data.curr_k["open"])
            is_strong_breakout = (
                (is_breakout_long and c_body >= 0.5 * market_data.atr)
                or (is_breakdown_short and c_body >= 0.5 * market_data.atr)
            )

            # 1. 平倉後強制冷卻檢查 (強動能突破放行交由 EntryGate 統一裁決)
            acc_key = cls._get_account_key(account, symbol)
            last_exit = cls._last_exit_record.get(acc_key)
            if last_exit and not is_strong_breakout:
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

            if can_long:
                p_up = market_data.prev_k.get("kc_upper", market_data.kc_upper) if market_data.prev_k else market_data.kc_upper
                p_close = market_data.prev_k.get("close", market_data.current_price) if market_data.prev_k else market_data.current_price
                if is_breakout_long:
                    candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if p_close > p_up else "BREAKOUT_LONG"
                elif is_ma_cross_long:
                    candidate_signal = "ENTRY_LONG_MA3_MA15_CROSS_MID"
                elif is_pullback_long:
                    candidate_signal = "TREND_PULLBACK_LONG"
                elif (market_data.curr_k["close"] > market_data.ema_fast and market_data.ema_fast >= market_data.ema_slow and market_data.kc_middle_slope >= 0 and ma5_curr > ma5_prev):
                    if market_data.curr_k["close"] > market_data.kc_upper:
                        candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if p_close > p_up else "BREAKOUT_LONG"
                    else:
                        candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"

            elif can_short:
                p_low = market_data.prev_k.get("kc_lower", market_data.kc_lower) if market_data.prev_k else market_data.kc_lower
                p_close = market_data.prev_k.get("close", market_data.current_price) if market_data.prev_k else market_data.current_price
                if is_breakdown_short:
                    candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if p_close < p_low else "BREAKDOWN_SHORT"
                elif is_ma_cross_short:
                    candidate_signal = "ENTRY_SHORT_MA3_MA15_CROSS_MID"
                elif is_pullback_short:
                    candidate_signal = "TREND_PULLBACK_SHORT"
                elif (market_data.curr_k["close"] < market_data.ema_fast and market_data.ema_fast <= market_data.ema_slow and market_data.kc_middle_slope <= 0 and ma5_curr < ma5_prev):
                    if market_data.curr_k["close"] < market_data.kc_lower:
                        candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if p_close < p_low else "BREAKDOWN_SHORT"
                    else:
                        candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"

            if candidate_signal:
                last_exit_context = {
                    **(last_exit or {}),
                    "hourly_entry_count": len(entry_history),
                    "current_time": now_ts,
                }
                should_entry, reason, meta = EntryGate.evaluate(
                    signal_type=candidate_signal,
                    market_data=market_data,
                    last_exit_context=last_exit_context,
                )
                if should_entry:
                    side = meta.get("side", "LONG" if "LONG" in candidate_signal else "SHORT")
                    logger.info(f"🎯 [ENTRY_TRIGGERED] {symbol} {side} {reason} | meta={meta}")
                    print(f"🎯 [ENTRY_TRIGGERED] {symbol} {side} {reason}", flush=True)
                    call_meta = dict(meta or {})
                    call_meta["candidate_signal_type"] = candidate_signal
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
                    success = await cls.open_position(
                        engine_or_account=engine_or_account,
                        symbol=symbol,
                        side=side,
                        price=market_data.current_price,
                        reason=f"EntryGate:{reason}",
                        meta=call_meta,
                    )
                    return {"status": "ENTRY_EXECUTED", "symbol": symbol, "side": side, "reason": reason, "success": success}
                else:
                    logger.debug(f"🚫 [VETO] {symbol} {reason}")
                    return {"status": "VETO", "symbol": symbol, "reason": reason}

        return {"status": "IDLE", "symbol": symbol}

    _last_exit_record: Dict[str, Dict[str, Any]] = {}
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

            atr = float((meta or {}).get("atr") or (price * 0.015))
            if str(side).upper() == "LONG":
                initial_sl = max(0.0, price - 3.0 * atr)
            else:
                initial_sl = price + 3.0 * atr

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
                "entry_type": "CROSS_MID" if is_cross_mid else "BREAKOUT",
                "initial_sl": initial_sl,
                "signal_id": signal_id,
                "channel_confirmation_bar_id": bar_id,
                "bar_id": bar_id,
                "dual_tf_meta": {"bar_id": bar_id},
                "is_intrabar": bool((meta or {}).get("intrabar_authorized")),
                "meta": meta or {},
            }
            if frame is not None:
                entry_context["entry_gate_frame"] = frame
                entry_context["frame"] = frame

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
