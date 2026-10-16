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


async def _process_wild_swing_symbol(
    engine, symbol, frame, quote, daily_halt, exit_only,
):
    from core.services.candle_data import closed_entry_candles
    from core.services.strategies.wild_swing_strategy import WildSwingStrategy

    strategy = getattr(engine, "_wild_swing_strategy", None)
    if strategy is None:
        strategy = engine._wild_swing_strategy = WildSwingStrategy()
    closed = closed_entry_candles(frame)
    if closed.empty or "timestamp" not in closed:
        return [], []

    latest_closed_bar_id = float(closed.iloc[-1]["timestamp"])
    if symbol not in strategy.states:
        strategy.reset_symbol(symbol, latest_closed_bar_id)

    if not hasattr(engine, "_channel_exit_frames"):
        engine._channel_exit_frames = {}
    engine._channel_exit_frames[symbol] = frame.copy()

    position = engine.account.positions.get(symbol)
    if position:
        from core.gates.holding_protection_gate import _ratchet_lock_details

        profit_lock = _ratchet_lock_details(position, quote)
        exit_decision = strategy.evaluate_exit(
            symbol,
            position,
            frame,
            profit_lock_triggered=profit_lock is not None,
        )
        if exit_decision is not None:
            from core.gates.trend_riding_strategy import AntiSlippageOrderExecutor

            limit_price = AntiSlippageOrderExecutor.calculate_limit_close_price(
                position["side"], quote, offset_pct=0.0025,
            )
            closed_ok = await engine.account.close_position(
                symbol,
                quote,
                "Wild Swing " + exit_decision["reason"],
                is_manual=True,
                is_limit=True,
                limit_price=limit_price,
            )
            if closed_ok and symbol not in engine.account.positions:
                exit_price = getattr(engine.account, "last_closed_price", {}).get(
                    symbol, quote,
                )
                strategy.record_exit(
                    symbol,
                    exit_decision["side"],
                    exit_price,
                    exit_decision["reason"],
                    exit_decision["bar_id"],
                )
        return [], []

    if exit_only or daily_halt or not math.isfinite(quote) or quote <= 0:
        return [], []
    if (
        symbol in getattr(engine.account, "closing_lock", set())
        or symbol in getattr(engine.account, "pending_limit_orders", {})
    ):
        return [], []

    from core.vision.chart_guard import (
        DynamicVisionGatekeeper,
        evaluate_qwen_entry_vision,
    )

    vision_gate = getattr(engine, "_wild_swing_vision_gate", None)
    if vision_gate is None:
        vision_gate = engine._wild_swing_vision_gate = DynamicVisionGatekeeper()
    seen_bar_ids = getattr(engine, "_wild_swing_vision_seen_bar_ids", None)
    if not isinstance(seen_bar_ids, dict):
        seen_bar_ids = engine._wild_swing_vision_seen_bar_ids = {}
    previous_vision_bar_id = seen_bar_ids.get(symbol)
    if previous_vision_bar_id is None:
        seen_bar_ids[symbol] = latest_closed_bar_id
    elif latest_closed_bar_id > previous_vision_bar_id:
        elapsed_bars = sum(
            float(bar_id) > previous_vision_bar_id
            for bar_id in closed["timestamp"]
        )
        for _ in range(elapsed_bars):
            vision_gate.on_bar_closed(symbol)
        seen_bar_ids[symbol] = latest_closed_bar_id

    profit_lock_exit = strategy.latest_profit_lock_exit(
        getattr(engine.account, "trades", None), symbol,
    )
    signals = [
        strategy.evaluate_entry(
            symbol, frame, side, profit_lock_exit=profit_lock_exit,
        )
        for side in ("LONG", "SHORT")
    ]
    signals = [signal for signal in signals if signal is not None]
    if len(signals) != 1:
        return [], []

    signal = signals[0]
    attempted_bar_ids = getattr(engine, "_wild_swing_vision_attempted_bar_ids", None)
    if not isinstance(attempted_bar_ids, dict):
        attempted_bar_ids = engine._wild_swing_vision_attempted_bar_ids = {}
    if attempted_bar_ids.get(symbol) == latest_closed_bar_id:
        return [], []
    attempted_bar_ids[symbol] = latest_closed_bar_id

    current_bar = closed.iloc[-1]
    vision_gate.clear_for_confirmed_ma_turn(
        symbol,
        entry_pattern=signal.get("entry_pattern"),
        structure_confirmation=signal.get("structure_confirmation"),
        structure_confirmed=signal.get("structure_confirmed") is True,
    )
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    macro_fields = (
        "open", "high", "low", "close", "kc_upper", "kc_lower",
        "kc_middle", "ma15", "atr",
    )
    missing_macro_fields = [field for field in macro_fields if field not in current_bar]
    if missing_macro_fields:
        macro_allowed = False
        macro_reason = (
            "VETO_INVALID_MARKET_DATA: missing "
            + ",".join(missing_macro_fields)
        )
    else:
        try:
            macro_bar = {
                field: float(current_bar[field])
                for field in ("open", "high", "low", "close")
            }
            macro_indicators = {
                field: float(current_bar[field])
                for field in ("kc_upper", "kc_lower", "kc_middle", "ma15", "atr")
            }
            for field in (
                "ma5_current", "ma5_previous",
                "kc_middle_current", "kc_middle_previous",
            ):
                if field in signal:
                    macro_indicators[field] = float(signal[field])
        except (TypeError, ValueError, OverflowError):
            macro_allowed = False
            macro_reason = "VETO_INVALID_MARKET_DATA: non-numeric structure"
        else:
            values = tuple(macro_bar.values()) + tuple(macro_indicators.values())
            if (
                not all(math.isfinite(value) for value in values)
                or macro_indicators["atr"] <= 0
                or macro_indicators["kc_upper"] <= macro_indicators["kc_lower"]
            ):
                macro_allowed = False
                macro_reason = "VETO_INVALID_MARKET_DATA: invalid KC/ATR values"
            else:
                macro_allowed, macro_reason = MacroCandleStructureGate().can_open(
                    signal["side"],
                    macro_bar,
                    macro_indicators,
                    entry_pattern=signal.get("entry_pattern"),
                    structure_confirmation=signal.get("structure_confirmation"),
                    structure_confirmed=signal.get("structure_confirmed") is True,
                    channel_expanding=signal.get("channel_expanding") is True,
                )
    if not macro_allowed:
        logger.info(
            "【GATE 拒絕】symbol=%s side=%s pattern=%s bar=%s signal=%s reason=%s",
            symbol, signal["side"], signal["entry_pattern"],
            latest_closed_bar_id, signal["reason"], macro_reason,
        )
        return [], []
    logger.info(
        "【GATE 通過: %s】symbol=%s side=%s bar=%s",
        signal["entry_pattern"], symbol, signal["side"], latest_closed_bar_id,
    )

    can_submit, gate_reason = vision_gate.can_submit_for_vision(
        symbol,
        current_bar,
        signal["side"],
        entry_pattern=signal.get("entry_pattern"),
        structure_confirmation=signal.get("structure_confirmation"),
        structure_confirmed=signal.get("structure_confirmed") is True,
    )
    if not can_submit:
        logger.info(
            "WILD_SWING_VISION_SKIPPED %s %s mode=%s bar=%s reason=%s",
            symbol, signal["side"], signal["entry_pattern"],
            latest_closed_bar_id, gate_reason,
        )
        return [], []

    logger.info(
        "【送審 AI 截圖...】source=local_qwen_vl symbol=%s side=%s pattern=%s bar=%s",
        symbol, signal["side"], signal["entry_pattern"], latest_closed_bar_id,
    )
    approved, vision_reason = await evaluate_qwen_entry_vision(
        closed, signal["side"], mode=signal["entry_pattern"],
    )
    ai_status = "APPROVED" if approved else "REJECTED"
    logger.info(
        "【AI 審查結果: %s】source=local_qwen_vl symbol=%s side=%s pattern=%s bar=%s reason=%s",
        ai_status, symbol, signal["side"], signal["entry_pattern"],
        latest_closed_bar_id, vision_reason,
    )
    if not approved:
        vision_gate.record_rejection(symbol, current_bar, signal["side"])
        logger.info(
            "【執行取消】symbol=%s side=%s pattern=%s bar=%s reason=%s",
            symbol, signal["side"], signal["entry_pattern"],
            latest_closed_bar_id, vision_reason,
        )
        return [], []
    vision_gate.record_approved(symbol)
    opened = await engine.execute_dual_tf_entry(
        symbol=symbol,
        frame=closed,
        price=quote,
        side=signal["side"],
        mode_name=f"WILD_SWING_{signal['reason']}",
        meta_dict=signal,
        entry_mode="WILD_SWING",
    )
    logger.info(
        "【執行下單】symbol=%s side=%s pattern=%s bar=%s result=%s",
        symbol, signal["side"], signal["entry_pattern"],
        latest_closed_bar_id, "FILLED" if opened else "REJECTED",
    )
    if opened and symbol in engine.account.positions:
        strategy.record_entry_fill(symbol, signal["bar_id"])
    return [], []


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
        from core.symbol_aliases import is_meme_breakout_symbol
        timeframe = "1m" if is_meme_breakout_symbol(symbol) else "5m"
        frame = await engine.fetch_klines(symbol, timeframe=timeframe, limit=150, keep_live=True)
        if frame is not None and not frame.empty:
            frame = engine.strategy.compute_indicators(frame.copy())

    if frame is None or frame.empty:
        return [], []

    from core.symbol_aliases import is_meme_breakout_symbol
    wild_swing_symbol = is_meme_breakout_symbol(symbol)
    if wild_swing_symbol:
        timeframe_ms = frame.attrs.get("timeframe_ms")
        if timeframe_ms is not None and float(timeframe_ms) != 60_000.0:
            frame = await engine.fetch_klines(
                symbol, timeframe="1m", limit=150, keep_live=True,
            )
            if frame is None or frame.empty:
                return [], []
            frame = engine.strategy.compute_indicators(frame.copy())
            if float(frame.attrs.get("timeframe_ms", 0) or 0) != 60_000.0:
                return [], []
        quote = float(
            exit_quote
            if exit_quote is not None
            else (getattr(engine, "tickers", {}).get(symbol) or frame.iloc[-1]["close"])
        )
        if not math.isfinite(quote) or quote <= 0:
            return [], []
        if daily_halt and symbol not in engine.account.positions:
            return [], []
        return await _process_wild_swing_symbol(
            engine, symbol, frame, quote, daily_halt, exit_only,
        )

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

        # ──【平倉冷卻期與二次進場回抽追蹤】──
        curr_ts = time.time() * 1000.0
        last_closed_ts = 0.0
        last_closed_action = ""
        last_closed_price = 0.0

        last_closed_raw = getattr(engine.account, 'last_closed_at', {}).get(symbol)
        if last_closed_raw:
            last_closed_ts = float(last_closed_raw) * 1000.0 if float(last_closed_raw) < 1e11 else float(last_closed_raw)
        last_closed_side_raw = getattr(engine.account, 'last_closed_side', {}).get(symbol)
        if last_closed_side_raw:
            last_closed_action = f"CLOSE_{last_closed_side_raw}"
        last_closed_price_raw = getattr(engine.account, 'last_closed_price', {}).get(symbol)
        if last_closed_price_raw:
            last_closed_price = float(last_closed_price_raw)

        for tr in getattr(engine.account, 'trades', []):
            if tr.get('symbol') == symbol and str(tr.get('action', '')).startswith('CLOSE_'):
                tr_ts = float(tr.get('timestamp') or tr.get('id') or 0.0)
                if tr_ts > 0:
                    if tr_ts < 1e11:
                        tr_ts *= 1000.0
                    if tr_ts > last_closed_ts or last_closed_ts == 0.0:
                        last_closed_ts = tr_ts
                        last_closed_action = str(tr.get('action', ''))
                        last_closed_price = float(tr.get('price', 0.0))

        # ──【平倉冷卻期：強制等待 5 根 1M K 棒 (300 秒 = 300,000 ms)】──
        # 徹底避免在同一個底部/頂部連續被巴
        COOLDOWN_MS = 300_000.0  # 強制等待 5 根 1M K 棒 (300 秒)
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
                last_closed_action=last_closed_action,
                last_closed_price=last_closed_price,
                last_closed_ts=last_closed_ts,
                curr_ts=curr_ts,
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
                # ──【平倉後冷卻期硬檢核 (Cooldown Hard Gate)】──
                # 使用者鐵律：「剛觸發平空/平多離場後，強制等待 5 根 1M K 棒 (300秒)，嚴禁同向連續開倉」！
                is_same_side_cooldown = (
                    (candidate_side == 'SHORT' and last_closed_action in ('CLOSE_SHORT', 'EXIT_SHORT')) or
                    (candidate_side == 'LONG' and last_closed_action in ('CLOSE_LONG', 'EXIT_LONG'))
                )
                if is_in_cooldown and is_same_side_cooldown:
                    remaining_s = (COOLDOWN_MS - (curr_ts - last_closed_ts)) / 1000.0
                    cooldown_msg = f"[{symbol}] [冷卻攔截 VETO] 平倉後強制冷卻 5 根 K 棒中，剩餘 {remaining_s:.1f} 秒，禁止開 {candidate_side} ({mode_name})"
                    logger.info(cooldown_msg)
                    print(cooldown_msg, flush=True)
                    continue

                # 最終下單入口二度方向與 MA5 硬鎖檢核
                m5_metrics = DualTimeframeProvider.get_5m_metrics(frame)
                ma5_s = m5_metrics.get('5m_ma5_slope', 0.0)
                ma5_v = m5_metrics.get('5m_ma5', 0.0)
                kc_mid = m5_metrics.get('5m_kc_middle', 0.0)
                # 均線間距 (>= 0.20 ATR) 與 MA5 斜率動能 (>= 0.05 / <= -0.05 ATR) 終極過濾 (Mode 3 斷頭鍘刀已放寬)
                if mode_name != 'MODE3_GUILLOTINE_CUT':
                    ma_ok, ma_reason = MemeSharedProtectionGate.validate_ma_condition(
                        frame, candidate_side, min_gap_atr=0.20, min_down_slope_atr=-0.05, min_up_slope_atr=0.05
                    )
                    if not ma_ok:
                        logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {ma_reason}")
                        continue

                # 平倉後回抽二次進場終極過濾 (Post-Exit Pullback Re-entry Filter)
                pb_ok, pb_reason = MemeSharedProtectionGate.validate_pullback_reentry(
                    df=frame,
                    side=candidate_side,
                    quote=quote,
                    last_closed_action=last_closed_action,
                    last_closed_price=last_closed_price,
                    last_closed_ts=last_closed_ts,
                    curr_ts=curr_ts,
                    atr=m5_metrics.get('5m_atr', 0.0),
                    ma5=ma5_v,
                )
                if not pb_ok:
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {pb_reason}")
                    continue

                # 終極「漲勢末端/跌勢末端」與「獲利空間」硬阻斷 (Final Exhaustion & Profit Room VETO)
                strong_bullish_trend = bool((meta_dict or {}).get('strong_bullish_trend', False))
                strong_bearish_trend = bool((meta_dict or {}).get('strong_bearish_trend', False))
                bullish_continuation = bool((meta_dict or {}).get('is_bullish_pullback_continuation', False))
                bearish_continuation = bool((meta_dict or {}).get('is_bearish_pullback_continuation', False))
                exh_ok, exh_reason = MemeSharedProtectionGate.validate_trend_exhaustion(
                    frame,
                    candidate_side,
                    quote,
                    strong_bullish_trend=strong_bullish_trend,
                    strong_bearish_trend=strong_bearish_trend,
                    bullish_continuation=bullish_continuation,
                    bearish_continuation=bearish_continuation,
                )
                if not exh_ok:
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {exh_reason}")
                    print(f"[{symbol}] [進場攔截 VETO] {exh_reason}", flush=True)
                    continue

                room_ok, room_reason = MemeSharedProtectionGate.validate_profit_room(frame, candidate_side, quote)
                if not room_ok:
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {room_reason}")
                    print(f"[{symbol}] [進場攔截 VETO] {room_reason}", flush=True)
                    continue

                if candidate_side == 'LONG':
                    price_ok = (quote >= ma5_v and quote >= kc_mid) if mode_name != 'MODE3_GUILLOTINE_CUT' else (quote >= min(ma5_v, kc_mid))
                    if ma5_s <= 0 or not price_ok:
                        logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked LONG due to non-positive MA5 slope ({ma5_s}) or price < MA5/Middle (quote={quote}, ma5={ma5_v}, mid={kc_mid})")
                        continue
                if candidate_side == 'SHORT':
                    price_ok = (quote <= ma5_v and quote <= kc_mid) if mode_name != 'MODE3_GUILLOTINE_CUT' else (quote <= max(ma5_v, kc_mid))
                    if ma5_s >= 0 or not price_ok:
                        logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked SHORT due to non-negative MA5 slope ({ma5_s}) or price > MA5/Middle (quote={quote}, ma5={ma5_v}, mid={kc_mid})")
                        continue

                # 終極「高低位追單、負乖離過大與針尖力竭」雙重把關 (Final Anti-Chase & Pinbar VETO)
                from core.gates.high_position_chase_and_defense_filter import HighPositionChaseAndDefenseFilter
                final_chase_filter = HighPositionChaseAndDefenseFilter()
                fc_ok, fc_reason, _ = final_chase_filter.validate_entry(
                    side=candidate_side,
                    df=frame,
                    quote=quote,
                    last_closed_action=last_closed_action,
                    last_closed_ts=last_closed_ts,
                    last_closed_price=last_closed_price,
                    timeframe_ms=60_000.0,
                    strong_trend=(
                        strong_bullish_trend if candidate_side == 'LONG'
                        else strong_bearish_trend
                    ),
                    bypass_extension_check=(
                        bullish_continuation if candidate_side == 'LONG'
                        else bearish_continuation
                    ),
                )
                if not fc_ok:
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {fc_reason}")
                    print(f"[{symbol}] [進場攔截 VETO] {fc_reason}", flush=True)
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
                if opened:
                    from core.gates.ratchet_net_profit_gate import get_ratchet_manager
                    get_ratchet_manager(symbol).reset()
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
        is_limit = bool((exit_details or {}).get('is_limit', False))
        limit_price = (exit_details or {}).get('limit_price', quote)
        # 即時重置持倉狀態機，原子清除部位與標記
        await engine.account.close_position(
            symbol,
            quote,
            f"DualTF {exit_reason}",
            is_manual=True,
            is_limit=is_limit,
            limit_price=limit_price,
        )
        engine.account.positions.pop(symbol, None)
        if hasattr(engine.account, 'position_meta'):
            engine.account.position_meta.pop(symbol, None)
        strict_strategy.set_state(symbol, PositionState.IDLE)
        if hasattr(engine.account, 'last_closed_at'):
            if isinstance(engine.account.last_closed_at, dict):
                engine.account.last_closed_at[symbol] = time.time()
        from core.gates.ratchet_net_profit_gate import get_ratchet_manager
        get_ratchet_manager(symbol).reset()
        return [], []

    return [], []
