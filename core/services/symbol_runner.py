import asyncio
import copy
import math
import time
import pandas as pd
from typing import Dict, Any, List, Tuple
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
from core.services.exits.dual_track_exit_service import (
    DUAL_TRACK_STATE_KEYS, DualTrackExitStrategy,
)
from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT

async def process_single_symbol_runner(
    engine: Any, symbol: str, now_time: float, btc_1m_turn: str | None, daily_halt: bool,
    exit_frame: pd.DataFrame | None = None, exit_quote: float | None = None, exit_only: bool = False
) -> Tuple[List[str], List[dict]]:
    signal_progress = []
    detected_candidates = []
    try:
        if exit_only and symbol not in engine.account.positions:
            return signal_progress, detected_candidates
            
        channel_df = exit_frame
        if channel_df is None:
            channel_df = await engine.fetch_klines(symbol, timeframe="1m", limit=200, keep_live=True)
            if not channel_df.empty:
                channel_df = engine.strategy.compute_indicators(channel_df.copy())
                
        if channel_df is None or channel_df.empty:
            return signal_progress, detected_candidates
            
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

        # 檢查冷卻時間 (最多一根 K 棒)
        current_bar_id = float(channel_df.iloc[-1].get("timestamp", channel_df.index[-1]))
        cooldown_cache = getattr(engine, "_last_exit_bar_id", {})
        last_exit_bar = cooldown_cache.get(symbol)
        
        # IN_POSITION 狀態 (已持倉)
        if existing_pos:
            engine._take_over_manual_position(symbol, existing_pos)
            managed_at = time.time()
            existing_pos["bot_last_managed_at"] = managed_at
            engine.account.position_meta.setdefault(symbol, {})["bot_last_managed_at"] = managed_at

            # 1. 優先執行硬止損
            if await enforce_hard_stop(engine.account, symbol, channel_price):
                return signal_progress, detected_candidates

            # 2. 雙軌平倉機制 (包含極端防禦與常規波段)
            meta = engine.account.position_meta.setdefault(symbol, {})
            for key in DUAL_TRACK_STATE_KEYS:
                if existing_pos.get(key) is None and meta.get(key) is not None:
                    existing_pos[key] = copy.deepcopy(meta[key])
            exit_strategy = DualTrackExitStrategy(fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)
            
            velocity_drop_ratio = engine.get_velocity_drop_ratio(symbol)
            exit_reason = exit_strategy.evaluate_exit(existing_pos, channel_df, channel_price, velocity_drop_ratio=velocity_drop_ratio)
            # Persist observations before any awaited order or account refresh.
            observed = {
                key: copy.deepcopy(existing_pos[key])
                for key in DUAL_TRACK_STATE_KEYS if existing_pos.get(key) is not None
            }
            if any(meta.get(key) != value for key, value in observed.items()):
                meta.update(observed)
                engine.account.save_state()
            
            if exit_reason:
                is_limit_exit = "LIMIT_EXIT" in exit_reason
                
                # --- V5.1.1 限價單保險裝置 (Safety Net for Limit Orders) ---
                if is_limit_exit:
                    limit_state = meta.setdefault("limit_exit_state", {})
                    if not limit_state:
                        limit_state["ticks"] = 0
                        limit_state["trigger_price"] = channel_price
                        limit_state["atr"] = float(channel_df.iloc[-2]["atr"]) if len(channel_df) >= 2 else 0.0
                    else:
                        limit_state["ticks"] += 1
                        
                    if limit_state["ticks"] >= 3:
                        deviation = abs(channel_price - limit_state.get("trigger_price", channel_price))
                        if deviation > 0.5 * limit_state.get("atr", 0):
                            engine.account.log(f"🚨 [限價單保險觸發] {symbol} 限價逾時且價格偏離 > 0.5 ATR，強制轉市價平倉！", "WARNING")
                            is_limit_exit = False
                            meta.pop("limit_exit_state", None)
                else:
                    meta.pop("limit_exit_state", None)
                    
                order_type_str = "限價單" if is_limit_exit else "市價單"
                
                engine.account.log(f"⚠️ [平倉觸發] {symbol} 滿足平倉條件: {exit_reason}，執行平倉 ({order_type_str})...", "INFO")
                closed = await engine.account.close_position(
                    symbol,
                    channel_price,
                    f"DualTrackExit {exit_reason}",
                    is_manual=True,
                    is_limit=is_limit_exit
                )
                
                # 平倉成功後，徹底重置狀態機，確保能進入 IDLE 重新掃描
                if closed and symbol not in engine.account.positions:
                    # 強制清除所有相關快取與標記
                    engine.account.position_meta.pop(symbol, None)
                    getattr(engine, "_channel_outer_reentry_after_exit", {}).pop(symbol, None)
                    getattr(engine, "_channel_pending_reverse_bar", {}).pop(symbol, None)
                    if hasattr(engine.account, "channel_profit_reentries"):
                        engine.account.channel_profit_reentries.pop(symbol, None)
                    
                    # 記錄本次平倉 K 棒 ID，作為冷卻判定基準 (最多一根K)
                    if not hasattr(engine, "_last_exit_bar_id"):
                        engine._last_exit_bar_id = {}
                    engine._last_exit_bar_id[symbol] = current_bar_id
                    
                    engine.account.log(f"✅ [狀態重置] {symbol} 平倉完成，已清空歷史狀態，次根 K 棒恢復掃描", "SUCCESS")
                return signal_progress, detected_candidates

        # IDLE 狀態 (空倉掃描)
        else:
            # --- V5.1 防禦性冷卻機制 (The Safety Net) ---
            # 防重複開倉冷卻 (平倉後必須至少等待 3 根 K 線的呼吸空間)
            if last_exit_bar is not None:
                if current_bar_id - last_exit_bar < 3 * 60 * 1000:  # 1m K線, 3根 = 3分鐘
                    return signal_progress, detected_candidates
                
            # 統一進場策略評估
            entry_strategy = UnifiedEntryStrategy()
            
            from core.engine import market_crash_entries_paused
            is_system_halted = market_crash_entries_paused(getattr(engine, "_market_crash_entry_cooldown_until", 0.0), time.time())
            if is_system_halted:
                return signal_progress, detected_candidates
                
            print(f"[UnifiedEntry] Evaluating {symbol} at {channel_price:.4f} (Bar ID: {current_bar_id})", flush=True)
            
            velocity_drop_ratio = engine.get_velocity_drop_ratio(symbol)
            for direct_side in ("LONG", "SHORT"):
                allowed, reason, entry_decision = entry_strategy.evaluate_entry(
                    channel_df, channel_price, direct_side, velocity_drop_ratio=velocity_drop_ratio
                )
                if not allowed or entry_decision.get("action") != "ENTER":
                    print(f"[{symbol}] {direct_side} Rejected: {reason}", flush=True)
                    continue
                engine.account.log(f"🚀 [進場觸發] {symbol} 滿足進場條件: {reason} ({direct_side})", "INFO")
                await engine._execute_confirmed_channel_break(
                    symbol, channel_df, channel_price, direct_side, daily_halt
                )
                break
            
        return signal_progress, detected_candidates

    except Exception as e:
        engine.account.log(f'⚠️ [{symbol}] 處理失敗: {e}', 'WARNING')
        import traceback
        traceback.print_exc()
    return signal_progress, detected_candidates
