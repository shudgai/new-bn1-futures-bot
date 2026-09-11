import asyncio
import copy
import math
import time
import pandas as pd
from typing import Dict, Any, List, Tuple
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.strategies.outer_strategy import aligned_entry, LIVE_OUTER_CODES, ENTRY_TREND_CODES, OUTER_CODES, TREND_CODES
from core.services.exits.profit_protection_service import protection
from core.services.exits.fading_exit_service import fading_ma3_turn, STATE_KEY as FADING_STATE_KEY, EXIT_REASON as FADING_EXIT_REASON, IMMEDIATE_EXIT_REASON
from core.guards.abnormal_guard import channel_adverse_exit_reason
from core.services.swing_service import channel_ck_exit_with_tolerance
from core.services.strategies.pivot_strategy import PIVOT_CODES
from core.config import (
    TAKER_FEE_RATE, SLIPPAGE_PCT, SCAN_1M_KLINE_LIMIT, CHANNEL_VOLUME_DECAY_EXIT_ENABLED,
)
from core.strategy import has_volume_divergence

def create_exit_ticket(symbol: str, position: dict, channel_action: dict, frame: Any = None) -> dict:
    """Build the post-exit reentry ticket for a Channel Swing pullback exit."""
    try:
        exit_bar_id = frame.iloc[-1].get("timestamp", frame.index[-1]) if frame is not None and not frame.empty else time.time() * 1000
    except (AttributeError, IndexError, TypeError):
        exit_bar_id = time.time() * 1000
    side = str(position.get("side") or "").upper()
    reason = channel_action.get("reason")
    fading_exit = reason == FADING_EXIT_REASON
    abnormal_exit = reason in {
        "KC_LONG_LIVE_RED_LONG_EXIT", "KC_SHORT_LIVE_GREEN_LONG_EXIT",
        "EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL", "EMERGENCY_EXIT_CLOSED_ADVERSE_WATERFALL",
        "EMERGENCY_EXIT_2_CANDLE_ADVERSE", "EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL",
    }
    token = str(position.get("open_timestamp")) + ":" + str(time.time_ns())
    return {
        "symbol": symbol,
        "token": token,
        "phase": "closing",
        "side": side,
        "old_side": side,
        "mode": "next_breakout" if fading_exit else "outer_cycle",
        "requires_pullback": bool(abnormal_exit),
        "close_reason": f"Channel Swing {reason}",
        "close_requested_at_ms": int(time.time() * 1000),
        "opened_at": position.get("open_timestamp"),
        "exit_bar_id": exit_bar_id,
    }

async def process_single_symbol_runner(
    engine: Any, symbol: str, now_time: float, btc_1m_turn: str | None, daily_halt: bool,
    exit_frame: pd.DataFrame | None = None, exit_quote: float | None = None, exit_only: bool = False
) -> Tuple[List[str], List[dict]]:
    signal_progress = []
    detected_candidates = []
    try:
        btc_pulse = str(btc_1m_turn or "").upper()
        if exit_only and symbol not in engine.account.positions:
            return signal_progress, detected_candidates
        channel_df = exit_frame
        if channel_df is None:
            channel_df = await engine.fetch_klines(symbol, timeframe="1m", limit=SCAN_1M_KLINE_LIMIT, keep_live=True)
            if not channel_df.empty:
                channel_df = engine.strategy.compute_indicators(channel_df.copy())
        if not channel_df.empty:
            cache = getattr(engine, "_channel_exit_frames", None)
            if cache is None:
                cache = engine._channel_exit_frames = {}
            cache[symbol] = channel_df.copy()
        channel_price = float(exit_quote if exit_quote is not None else (
            engine.tickers.get(symbol)
            or (channel_df["close"].iloc[-1] if not channel_df.empty else 0.0)))
        existing_pos = engine.account.positions.get(symbol)
        if exit_only and not existing_pos:
            return signal_progress, detected_candidates
        if existing_pos:
            if await enforce_hard_stop(engine.account, symbol, channel_price):
                return signal_progress, detected_candidates
            if await engine._try_ck_reverse(symbol, channel_df, channel_price, daily_halt):
                return signal_progress, detected_candidates
        if not existing_pos:
            if await engine._try_live_pivot_entry(symbol, channel_df, channel_price, daily_halt):
                return signal_progress, detected_candidates
            entry_side = aligned_entry(channel_df, channel_price).get("side")
            engine._channel_intrabar_ready(symbol, channel_df, channel_price, entry_side)
        else:
            watcher = getattr(engine, "_channel_intrabar_entries", None)
            if watcher is not None:
                watcher.reset(symbol)
        if existing_pos:
            engine._take_over_manual_position(symbol, existing_pos)
            managed_at = time.time()
            existing_pos["bot_last_managed_at"] = managed_at
            engine.account.position_meta.setdefault(symbol, {})["bot_last_managed_at"] = managed_at

        chop_state = engine._channel_chop_state(channel_df)
        btc_lead_candidate = None
        if not existing_pos and btc_pulse in ("LONG", "SHORT"):
            btc_lead_candidate = engine._record_btc_lead_shadow_candidate(
                symbol, channel_df, channel_price, False,
            )
        channel_market_mode = engine._channel_macro_market_mode(symbol)
        channel_exit_net_profitable = True
        if existing_pos:
            estimated_exit_net = engine._channel_takeover_net_pnl(
                existing_pos, channel_price,
            )
            channel_exit_net_profitable = bool(
                math.isfinite(estimated_exit_net)
                and estimated_exit_net > 0.0
            )
        path_state = existing_pos.setdefault("channel_position_path", {}) if existing_pos else None
        path_before = dict(path_state) if path_state is not None else None
        channel_action = engine._channel_swing_action(
            channel_df, channel_price,
            existing_pos.get("side") if existing_pos else None,
            existing_pos.get("channel_turn_low") if existing_pos else None,
            existing_pos.get("channel_turn_high") if existing_pos else None,
            channel_market_mode,
            existing_pos.get("open_timestamp") if existing_pos else None,
            position_path=path_state,
            exit_net_profitable=channel_exit_net_profitable,
            entry_kc_upper=existing_pos.get("entry_kc_upper") if existing_pos else None,
            entry_kc_lower=existing_pos.get("entry_kc_lower") if existing_pos else None,
            entry_outer_chase=bool(
                existing_pos.get("outer_chase_entry")
                or "live KC outer break" in str(existing_pos.get("reason") or "")
            ) if existing_pos else False,
            profit_locked=bool(
                existing_pos.get("channel_cross_lock")
                or engine.account.position_meta.get(symbol, {}).get("channel_cross_lock")
                or existing_pos.get("is_breakeven_moved")
                or engine.account.position_meta.get(symbol, {}).get("is_breakeven_moved")
            ) if existing_pos else False,
            cross_timer_start=float(
                existing_pos.get("channel_cross_timer")
                or engine.account.position_meta.get(symbol, {}).get("channel_cross_timer")
                or 0.0
            ) if existing_pos else 0.0,
            allow_live_entry=not bool(existing_pos),
        )
        if (
            not existing_pos
            and channel_action.get("action") == "ENTER"
            and channel_action.get("reason") not in LIVE_OUTER_CODES | ENTRY_TREND_CODES
            and chop_state.get("detected")
            and not chop_state.get("clear_direction")
        ):
            breakout = engine._channel_chop_breakout_action(channel_df, channel_price)
            if not (breakout.get("action") == "ENTER"
                    and breakout.get("side") == channel_action.get("side")):
                channel_action = {
                    "action": "WAIT", "side": None,
                    "reason": "CHOP_WAIT_NO_ENTRY",
                }
        tickets = getattr(engine.account, "channel_profit_reentries", None)
        if tickets is None:
            tickets = engine.account.channel_profit_reentries = {}
        if not existing_pos:
            engine._release_resolved_abnormal_exit(symbol, channel_df, channel_price)
        if not existing_pos and symbol in tickets:
            await engine._try_profit_reentry(symbol, channel_df, channel_price, daily_halt)
            return signal_progress, detected_candidates
        if existing_pos:
            if await enforce_hard_stop(engine.account, symbol, channel_price):
                return signal_progress, detected_candidates
            previous_protection = copy.deepcopy(existing_pos.get("channel_profit_protection"))
            profit = protection(existing_pos, channel_price, TAKER_FEE_RATE, SLIPPAGE_PCT, frame=channel_df)
            # 交易所帳戶（testnet/實盤）每次 refresh() 都會用交易所資料重建 position
            # 字典，寫在 position 上的階梯狀態會被清掉（峰值歸零 → 階梯永遠不觸發）。
            # 這裡把狀態同步回 position_meta，跨 refresh 與重啟都能保留。
            if previous_protection != existing_pos.get("channel_profit_protection"):
                engine.account.position_meta.setdefault(symbol, {})[
                    "channel_profit_protection"
                ] = copy.deepcopy(existing_pos.get("channel_profit_protection"))
                engine.account.save_state()

            meta = engine.account.position_meta.setdefault(symbol, {})
            if FADING_STATE_KEY not in existing_pos and FADING_STATE_KEY in meta:
                existing_pos[FADING_STATE_KEY] = copy.deepcopy(meta[FADING_STATE_KEY])
            before_turn = copy.deepcopy(existing_pos.get(FADING_STATE_KEY))
            terminal_turn = fading_ma3_turn(existing_pos, channel_df, channel_price)
            after_turn = existing_pos.get(FADING_STATE_KEY)
            if after_turn is None:
                meta.pop(FADING_STATE_KEY, None)
            else:
                meta[FADING_STATE_KEY] = copy.deepcopy(after_turn)
            if before_turn != after_turn:
                engine.account.save_state()

            changed = False
            stale_keys = ["channel_live_ma3_exit_pending", "channel_live_ma3_favorable_bar",
                          "channel_outer_ma3_turn_exit_pending", "channel_live_ma3_turn_exit_pending",
                          "channel_ma3_turn_observed_bar", "channel_significant_ma3_turn",
                          "channel_immediate_ma3_turn"]
            for state in (existing_pos, engine.account.position_meta.get(symbol, {})):
                if state.get("channel_exception_exit_pending") == "EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL":
                    state.pop("channel_exception_exit_pending")
                    changed = True
                for key in stale_keys:
                    if key in state:
                        state.pop(key)
                        changed = True
            channel_action = {"action": "HOLD", "side": None, "reason": "KC_WAIT_NET_PROFIT_GIVEBACK"}
            ck_exit = channel_ck_exit_with_tolerance(channel_df, existing_pos.get("side"), existing_pos)
            emergency = engine._channel_exception_exit(existing_pos, channel_df, channel_price)
            if emergency:
                if existing_pos.get("channel_exception_exit_pending") != emergency:
                    existing_pos["channel_exception_exit_pending"] = emergency
                    changed = True
                channel_action = {"action": "EXIT", "side": None, "reason": emergency}
            elif ck_exit:
                channel_action = {"action": "EXIT", "side": None, "reason": ck_exit}
            volume_decay_exit = bool(
                CHANNEL_VOLUME_DECAY_EXIT_ENABLED and not emergency
                and channel_action.get("reason") != FADING_EXIT_REASON
                and channel_exit_net_profitable
                and has_volume_divergence(
                    channel_df, -1 if existing_pos.get("side") == "LONG" else 1)
            )
            if terminal_turn and not emergency and not (profit and profit["triggered"]):
                channel_action = {"action": "EXIT", "side": None, "reason": FADING_EXIT_REASON}
            elif volume_decay_exit:
                channel_action = {"action": "EXIT", "side": None, "reason": "VOLUME_DECAY_EXIT"}
            if changed:
                engine.account.save_state()
            if channel_action.get("action") in {"EXIT", "REVERSE"}:
                if tickets.pop(symbol, None) is not None:
                    engine.account.save_state()
            elif profit and profit["triggered"]:
                token = str(existing_pos.get("open_timestamp")) + ":" + str(time.time_ns())
                tickets[symbol] = {"token": token, "phase": "closing",
                                   "side": existing_pos["side"],
                                   "old_side": existing_pos["side"], "mode": "outer_cycle",
                                   "close_reason": "Channel Swing PROFIT_PROTECTION " + token,
                                   "close_requested_at_ms": int(time.time() * 1000),
                                   "requires_pullback": False,
                                   "opened_at": existing_pos.get("open_timestamp"),
                                   "exit_bar_id": channel_df.iloc[-1].get("timestamp", channel_df.index[-1]),
                                   "path": copy.deepcopy(path_state)}
                engine.account.save_state()
                engine.account.log(f"🛡️ [獲利保護] {symbol} 階梯鎖利 淨利峰值={profit['peak_net']:.4f} 鎖定淨利={profit['locked_net']:.2f} 保護價={profit['stop_price']:.10g} 預估淨利={profit['net_pnl']:.4f}", "INFO")
                closed = await engine.account.close_position(
                    symbol, channel_price, "Channel Swing PROFIT_PROTECTION " + token, is_manual=True)
                if closed and symbol not in engine.account.positions:
                    tickets[symbol]["phase"] = "closed"
                    engine.account.save_state()
                    await engine._try_profit_reentry(symbol, channel_df, channel_price, daily_halt)
                else:
                    tickets.pop(symbol, None)
                    engine.account.save_state()
                return signal_progress, detected_candidates
        if path_state != path_before and hasattr(engine.account, "save_state"):
            engine.account.save_state()
        break_reasons = PIVOT_CODES | OUTER_CODES | LIVE_OUTER_CODES | TREND_CODES | {
            "KC_UPPER_BREAKOUT", "KC_LOWER_BREAKOUT",
            "KC_LIVE_UPPER_BREAK_LONG", "KC_LIVE_LOWER_BREAK_SHORT",
            "KC_LIVE_UPPER_MOMENTUM_LONG",

            "KC_UPPER_BREAKOUT_STRICT", "KC_LOWER_BREAKOUT_STRICT",
            "KC_CONTINUATION_LONG", "KC_CONTINUATION_SHORT",
            "KC_UPPER_TREND_ENTRY", "KC_LOWER_TREND_ENTRY",
        }
        pending_side = getattr(engine, "_channel_outer_reentry_after_exit", {}).get(symbol)
        direct_side = channel_action.get("side") if channel_action.get("reason") in break_reasons else None
        if not existing_pos and pending_side in ("LONG", "SHORT"):
            direct_side = None
            retry_bar = getattr(engine, "_channel_pending_reverse_bar", {}).get(symbol)
            if retry_bar == (pending_side, engine._channel_candidate_bar_id(channel_df)):
                retry_decision = engine._channel_swing_action(
                    channel_df, channel_price, outer_entry_only=True,
                )
                if retry_decision.get("side") == pending_side:
                    direct_side = pending_side
            if direct_side != pending_side:
                engine._channel_outer_reentry_after_exit.pop(symbol, None)
                getattr(engine, "_channel_pending_reverse_bar", {}).pop(symbol, None)
        if direct_side in ("LONG", "SHORT"):
            await engine._execute_confirmed_channel_break(symbol, channel_df, channel_price, direct_side, daily_halt)
            return signal_progress, detected_candidates
        if existing_pos and channel_action.get("action") == "EXIT":
            if channel_action.get("reason") == "KC_REACHED_MIDDLE_COMPRESSED":
                rows = channel_df.iloc[-2:]
                details = []
                for label, (_, row) in zip(("closed", "live"), rows.iterrows()):
                    upper, lower, ma15 = (float(row[key]) for key in ("kc_upper", "kc_lower", "ma15"))
                    rail = upper if existing_pos.get("side") == "LONG" else lower
                    ratio = abs(ma15 - rail) / (upper - lower)
                    details.append(f"{label}: bar={row.get('timestamp')} space={ratio:.6%} upper={upper:.10g} lower={lower:.10g} ma15={ma15:.10g} ma3={float(row['ma3']):.10g}")
                engine.account.log(f"[KC 平倉條件] {symbol} price={channel_price:.10g} " + " | ".join(details), "INFO")
            abnormal_exit = channel_action.get("reason") in {
                "KC_LONG_LIVE_RED_LONG_EXIT", "KC_SHORT_LIVE_GREEN_LONG_EXIT",
                "EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL", "EMERGENCY_EXIT_CLOSED_ADVERSE_WATERFALL",
                "EMERGENCY_EXIT_2_CANDLE_ADVERSE", "EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL",
                "EMERGENCY_EXIT_MA3_OUTSIDE_ADVERSE_BAR",
            }
            ma3_turn_exit = channel_action.get("reason", "").endswith("LIVE_MA3_TURN_EXIT")
            fading_exit = channel_action.get("reason") == FADING_EXIT_REASON
            immediate_turn_exit = channel_action.get("reason") == IMMEDIATE_EXIT_REASON
            ck_exit = channel_action.get("reason") in {
                "KC_CK_DIRECTION_UNCLEAR_EXIT", "KC_CK_DIRECTION_REVERSED_EXIT",
            }
            pullback_exit = (fading_exit or abnormal_exit or ma3_turn_exit
                             or immediate_turn_exit or ck_exit or channel_exit_net_profitable)
            if pullback_exit:
                tickets[symbol] = create_exit_ticket(symbol, existing_pos, channel_action, channel_df)
                engine.account.save_state()
            closed = await engine.account.close_position(
                symbol,
                channel_price,
                f"Channel Swing {channel_action.get('reason')}",
                is_manual=True,
            )
            if pullback_exit:
                if closed and symbol not in engine.account.positions:
                    tickets[symbol]["phase"] = "closed"
                    engine.account.log(f"⏳ [平倉後重開] {symbol} " + ("正常平倉下一根起MA3軌外順向可延續，不必回調" if fading_exit else "異常出場，等後續K回到CK內再順向站回外軌" if abnormal_exit else "下一根起MA3仍在同側外軌外可延續，無需回調或重新穿軌"), "INFO")
                else:
                    tickets.pop(symbol, None)
                engine.account.save_state()
            if closed and symbol not in engine.account.positions:
                if not hasattr(engine, "_channel_swing_peak_exit_info"):
                    engine._channel_swing_peak_exit_info = {}
                last_bar = channel_df.iloc[-1].get("timestamp", channel_df.index[-1]) if channel_df is not None and not channel_df.empty else time.time() * 1000
                engine._channel_swing_peak_exit_info[symbol] = {
                    "side": existing_pos.get("side"), "exit_bar_id": last_bar,
                    "require_new_closed_break": True, "allow_new_outer_signal": True,
                }
                getattr(engine, "_channel_outer_reentry_after_exit", {}).pop(symbol, None)
                getattr(engine, "_channel_pending_reverse_bar", {}).pop(symbol, None)
                engine.account.log(
                    f"✅ [Channel Swing 趨勢檢查] {symbol} 已平倉，後續按MA3軌外條件及票據風控重新評估",
                    "SUCCESS",
                )
            return signal_progress, detected_candidates
        engine._record_channel_signal_event(symbol, channel_action.get("reason"), channel_df)
        return signal_progress, detected_candidates

    except Exception as e:
        engine.account.log(f'⚠️ [{symbol}] 處理失敗: {e}', 'WARNING')
    return signal_progress, detected_candidates
