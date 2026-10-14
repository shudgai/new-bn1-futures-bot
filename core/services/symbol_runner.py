"""Live entries and indicator refresh; held exits use the 5M+15M dual timeframe policy."""
import math
import time
import logging
from core.services.strategies.strict_state_machine import StrictStateMachineStrategy, PositionState
from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate
from core.gates.trend_holding_immunity_and_exit_gate import TrendHoldingAndExitManager

logger = logging.getLogger("uvicorn.error")

# 全域單一策略實例
strict_strategy = StrictStateMachineStrategy()


async def process_single_symbol_runner(engine, symbol, now_time, btc_1m_turn, daily_halt,
                                       exit_frame=None, exit_quote=None, exit_only=False):
    # 1. 狀態與交易所同步（防重啟失步）
    position = engine.account.positions.get(symbol)
    if position:
        side = position.get("side")
        entry_price = float(position.get("entry_price", 0.0))
        max_profit = float(position.get("max_pnl_usdt", 0.0))
        if side == "LONG":
            strict_strategy.set_state(symbol, PositionState.LONG, {"entry_price": entry_price, "max_pnl": max_profit})
        elif side == "SHORT":
            strict_strategy.set_state(symbol, PositionState.SHORT, {"entry_price": entry_price, "max_pnl": max_profit})
    else:
        strict_strategy.set_state(symbol, PositionState.IDLE)

    # 2. 獲取 5M 操作級別 K 棒
    frame = exit_frame
    if frame is None:
        frame = await engine.fetch_klines(symbol, timeframe='5m', limit=150, keep_live=True)
        if frame is not None and not frame.empty:
            frame = engine.strategy.compute_indicators(frame.copy())

    if frame is None or frame.empty:
        return [], []

    if not hasattr(engine, '_channel_exit_frames'):
        engine._channel_exit_frames = {}
    engine._channel_exit_frames[symbol] = frame.copy()

    # 3. 獲取 15M 大趨勢級別 K 棒
    frame_15m = None
    try:
        frame_15m = await engine.fetch_klines(symbol, timeframe='15m', limit=80, keep_live=True)
        if frame_15m is not None and not frame_15m.empty:
            frame_15m = engine.strategy.compute_indicators(frame_15m.copy())
    except Exception:
        pass

    c_5m = float(frame.iloc[-1]['close']) if frame is not None and not frame.empty else None
    len_5m = len(frame) if frame is not None else 0
    len_15m = len(frame_15m) if frame_15m is not None else 0

    quote = float(exit_quote if exit_quote is not None else
                  (getattr(engine, 'tickers', {}).get(symbol) or frame.iloc[-1]['close']))

    if not math.isfinite(quote) or quote <= 0:
        return [], []

    if daily_halt and not position:
        logger.error(f"[{symbol}] [RUNNER_PROBE] 5M_Close: {quote} | 15M_Trend: None | Action: REJECT | RejectReason: DAILY_HALT")
        print(f"[{symbol}] [RUNNER_PROBE] 5M_Close: {quote} | 15M_Trend: None | Action: REJECT | RejectReason: DAILY_HALT", flush=True)
        return [], []

    from core.indicators.dual_timeframe_provider import DualTimeframeProvider
    trend_15m_dict = DualTimeframeProvider.get_15m_trend(frame_15m) if frame_15m is not None else {}
    trend_15m = trend_15m_dict.get('15m_big_trend', 'UNKNOWN')

    if position:
        probe_msg = (
            f"[{symbol}] [RUNNER_PROBE] len_5M: {len_5m} | len_15M: {len_15m} | "
            f"5M_Close: {c_5m} | 15M_Trend: {trend_15m} | Pos: {position.get('side')} | "
            f"Action: HOLD | RejectReason: ALREADY_IN_POSITION"
        )
        logger.error(probe_msg)
        print(probe_msg, flush=True)

    # 4. 更新持倉 PnL 與峰值
    unrealized_pnl = 0.0
    if position:
        entry_price = float(position.get("entry_price", 0.0))
        amount = float(position.get("amount", 0.0))
        if position.get("side") == "LONG":
            unrealized_pnl = (quote - entry_price) * amount
        elif position.get("side") == "SHORT":
            unrealized_pnl = (entry_price - quote) * amount

        position["unrealized_pnl"] = unrealized_pnl
        if unrealized_pnl > position.get("max_pnl_usdt", 0.0):
            position["max_pnl_usdt"] = unrealized_pnl

    # ══════════════════════════════════════════════════════════════════════
    # 【情況 A：當前「無持倉」 (IDLE) - 5M+15M 跨週期共振開倉】
    # ══════════════════════════════════════════════════════════════════════
    if not position:
        if exit_only:
            return [], []

        # ──【平倉冷卻期：常規縮短至 20 秒 (20,000 ms)】──
        curr_ts = float(frame.iloc[-1].get('timestamp', time.time() * 1000))
        last_closed_ts = 0.0
        last_closed_raw = getattr(engine.account, 'last_closed_at', {}).get(symbol)
        if last_closed_raw:
            last_closed_ts = float(last_closed_raw) * 1000.0 if float(last_closed_raw) < 1e11 else float(last_closed_raw)
        for tr in reversed(getattr(engine.account, 'trades', [])):
            if tr.get('symbol') == symbol and str(tr.get('action', '')).startswith('CLOSE_'):
                tr_ts = float(tr.get('timestamp') or tr.get('id') or 0.0)
                if tr_ts > 0:
                    if tr_ts < 1e11:
                        tr_ts *= 1000.0
                    if tr_ts > last_closed_ts:
                        last_closed_ts = tr_ts
                break

        COOLDOWN_MS = 20_000.0  # 常規平倉冷卻時間縮短至 20 秒 (20,000 ms)
        is_in_cooldown = (last_closed_ts > 0) and ((curr_ts - last_closed_ts) < COOLDOWN_MS)

        # 分別評估做多與做空潛力
        from core.services.entry_contract import evaluate_entry_contract
        from core.services.candle_data import log_entry_gate

        # 先以 5M+15M 跨週期共振閘門審查
        from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend
        from core.indicators.dual_timeframe_provider import DualTimeframeProvider
        kc_trend_5m = resolve_kc_trend(frame)
        trend_15m_dict = DualTimeframeProvider.get_15m_trend(frame_15m) if frame_15m is not None else {}
        trend_15m = trend_15m_dict.get('15m_big_trend', 'UNKNOWN')

        for candidate_side in ('LONG', 'SHORT'):
            can_enter, gate_reason, meta_dict = MemeSharedProtectionGate.evaluate_entry(
                df_5m=frame, df_15m=frame_15m, quote=quote, side=candidate_side, symbol=symbol,
            )
            action = 'ENTER' if can_enter else 'REJECT'
            probe_msg = (
                f"[{symbol}] [RUNNER_PROBE] len_5M: {len_5m} | len_15M: {len_15m} | "
                f"5M_Close: {c_5m} | 15M_Trend: {trend_15m} | Side: {candidate_side} | "
                f"Action: {action} | RejectReason: {gate_reason}"
            )
            logger.error(probe_msg)
            print(probe_msg, flush=True)
            if can_enter:
                mode_name = (meta_dict or {}).get('mode', 'MODE_DUAL_TF')
                # 強趨勢冷卻豁免：若 15M 大趨勢明確且觸發模式一（破軌）或模式三（斷頭鍘刀），直接豁免冷卻期，秒級開倉！
                if is_in_cooldown:
                    if mode_name in ('MODE1_KC_BREAKOUT', 'MODE3_GUILLOTINE_CUT'):
                        logger.info(f"[{symbol}] BYPASS_COOLDOWN: Strong trend {mode_name} bypasses {COOLDOWN_MS/1000}s cooldown!")
                    else:
                        logger.info(f"[{symbol}] In cooldown ({COOLDOWN_MS/1000}s), blocked {mode_name}")
                        continue

                # 最終下單入口二度方向與 MA5 硬鎖檢核
                m5_metrics = DualTimeframeProvider.get_5m_metrics(frame)
                ma5_s = m5_metrics.get('5m_ma5_slope', 0.0)
                ma5_v = m5_metrics.get('5m_ma5', 0.0)
                kc_mid = m5_metrics.get('5m_kc_middle', 0.0)
                # 均線間距 (>= 0.20 ATR) 與 MA5 斜率動能 (>= 0.05 / <= -0.05 ATR) 終極過濾
                ma_ok, ma_reason = MemeSharedProtectionGate.validate_ma_condition(
                    frame, candidate_side, min_gap_atr=0.20, min_down_slope_atr=-0.05, min_up_slope_atr=0.05
                )
                if not ma_ok:
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {ma_reason}")
                    continue

                if candidate_side == 'LONG' and (ma5_s <= 0 or quote < ma5_v or quote < kc_mid):
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked LONG due to non-positive MA5 slope ({ma5_s}) or price < MA5/Middle")
                    continue
                if candidate_side == 'SHORT' and (ma5_s >= 0 or quote > ma5_v or quote > kc_mid):
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked SHORT due to non-negative MA5 slope ({ma5_s}) or price > MA5/Middle")
                    continue

                auth_log = (f"[5M_15M_DUAL_TF_AUTHORIZED] Symbol: {symbol} | "
                            f"Mode: {mode_name} | Side: {candidate_side} | Price: {quote}")
                logger.info(auth_log)
                print(auth_log, flush=True)

                # 實盤下單直通（Force Direct Order Execution）
                opened = await engine.execute_dual_tf_entry(
                    symbol=symbol,
                    frame=frame,
                    price=quote,
                    side=candidate_side,
                    mode_name=mode_name,
                    meta_dict=meta_dict,
                )
                if opened and hasattr(engine.account, 'last_closed_at'):
                    if isinstance(engine.account.last_closed_at, dict):
                        engine.account.last_closed_at.pop(symbol, None)
                return [], []
            else:
                if gate_reason and "INSUFFICIENT" not in gate_reason:
                    logger.info(f"[{symbol}] 5M+15M Gate rejected {candidate_side}: {gate_reason}")


        return [], []

    # ══════════════════════════════════════════════════════════════════════
    # 【情況 B：當前「有持倉」 (IN_LONG / IN_SHORT) - 5M+15M 持倉護航與兩道出場】
    # ══════════════════════════════════════════════════════════════════════
    can_exit, exit_reason, exit_details = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=frame, df_15m=frame_15m, quote=quote, candidate_reason=None,
    )

    if can_exit:
        exit_log = f"[DUAL_TF_EXIT_TRIGGERED] Symbol: {symbol} | Reason: {exit_reason} | Price: {quote}"
        logger.info(exit_log)
        print(exit_log, flush=True)
        # 即時重置持倉狀態機，原子清除部位與標記
        await engine.account.close_position(symbol, quote, f"DualTF {exit_reason}", is_manual=True)
        engine.account.positions.pop(symbol, None)
        if hasattr(engine.account, 'position_meta'):
            engine.account.position_meta.pop(symbol, None)
        strict_strategy.set_state(symbol, PositionState.IDLE)
        if hasattr(engine.account, 'last_closed_at'):
            if isinstance(engine.account.last_closed_at, dict):
                engine.account.last_closed_at[symbol] = time.time()
        return [], []

    return [], []
