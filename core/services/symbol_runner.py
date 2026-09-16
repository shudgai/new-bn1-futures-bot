import asyncio
import copy
import math
import time
import pandas as pd
from typing import Dict, Any, List, Tuple
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
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
            exit_strategy = DualTrackExitStrategy(fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)
            exit_reason = exit_strategy.evaluate_exit(existing_pos, channel_df, channel_price)
            
            if exit_reason:
                engine.account.log(f"⚠️ [平倉觸發] {symbol} 滿足平倉條件: {exit_reason}，執行平倉...", "INFO")
                closed = await engine.account.close_position(
                    symbol,
                    channel_price,
                    f"DualTrackExit {exit_reason}",
                    is_manual=True,
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
            # 防重複開倉冷卻 (平倉當根禁止開倉，換根即可)
            if last_exit_bar is not None and last_exit_bar == current_bar_id:
                return signal_progress, detected_candidates
                
            # 統一進場策略評估
            entry_strategy = UnifiedEntryStrategy()
            entry_decision = entry_strategy.evaluate_entry(channel_df, channel_price)
            
            if entry_decision and entry_decision.get("action") == "ENTER":
                direct_side = entry_decision.get("side")
                reason = entry_decision.get("reason", "UNIFIED_ENTRY")
                engine.account.log(f"🚀 [進場觸發] {symbol} 滿足進場條件: {reason} ({direct_side})", "INFO")
                
                await engine._execute_confirmed_channel_break(
                    symbol, channel_df, channel_price, direct_side, daily_halt
                )
            
        return signal_progress, detected_candidates

    except Exception as e:
        engine.account.log(f'⚠️ [{symbol}] 處理失敗: {e}', 'WARNING')
    return signal_progress, detected_candidates
