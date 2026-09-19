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
                
                if exit_reason == "PARTIAL_TAKE_PROFIT":
                    engine.account.log(f"⚠️ [減倉觸發] {symbol} 滿足減倉條件: {exit_reason}，執行減半倉 ({order_type_str})...", "INFO")
                    closed = await engine.account.partial_close_position(
                        symbol,
                        channel_price,
                        f"DualTrackExit {exit_reason}",
                        fraction=0.5
                    )
                    if closed:
                        existing_pos["has_warning_partial_close"] = True
                        meta["has_warning_partial_close"] = True
                        # 重新對齊剩餘倉位的保底鎖利 (強制重新計算鎖利距離)
                        existing_pos.pop("v10_phase_trailing", None)
                        engine.account.log(f"🔄 [鎖利對齊] {symbol} 50% 減倉成功，已強制清除 v10_phase_trailing 狀態，下一根 K 棒將依據剩餘倉位重新計算並對齊保底鎖利點", "INFO")
                        engine.account.save_state()
                else:
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
                    
                    # 換手票據發放條件：
                    # 1. 對側破軌轉向 (V10 護衛型接力)
                    # 2. 峰谷三點結構瓦解 → 瞬間轉向捕捉對向動能噴發
                    # 3. Phase Trail 鎖利觸發 → 獲利鎖定後轉向評估
                    # 換手票據發放條件：
                    # 依據最新防禦架構，嚴格限制只有「峰谷瓦解 (PEAK_EXHAUSTION_EXIT)」才能觸發動能接力
                    is_structural_reversal = ("PEAK_EXHAUSTION_EXIT" in exit_reason)
                    if is_structural_reversal:
                        # ── 獲利墊片檢查 (Profit-Buffered Relay) ──
                        # 確保我們只有在「有獲利」時才進行轉向接力，若平手或虧損則只平倉不接力
                        entry_p = float(existing_pos.get("entry_price", channel_price))
                        qty     = float(existing_pos.get("size") or existing_pos.get("qty") or 0)
                        side    = existing_pos.get("side", "")
                        if side == "LONG":
                            gross_pnl = (channel_price - entry_p) * qty
                        else:
                            gross_pnl = (entry_p - channel_price) * qty
                        
                        fee_cost = (entry_p + channel_price) * qty * TAKER_FEE_RATE + channel_price * qty * SLIPPAGE_PCT
                        current_profit_usdt = gross_pnl - fee_cost

                        if current_profit_usdt > 2.0:
                            if not hasattr(engine, "_direct_reverse_ticket"):
                                engine._direct_reverse_ticket = {}
                            engine._direct_reverse_ticket[symbol] = current_bar_id
                            engine.account.log(
                                f"🎫 [獲利墊片接力] {symbol} [{exit_reason}] 結構平倉 (淨利 {current_profit_usdt:.2f}U > 2U)，發放接力票據", 
                                "SUCCESS"
                            )

                            # 趨勢接力狀態機（WATERFALL/DOUBLE_ABNORMAL 緊急平倉不接力）
                            is_emergency = ("WATERFALL" in exit_reason or "DOUBLE_ABNORMAL" in exit_reason)
                            
                            if not is_emergency:
                                relay_dir = "LONG" if "SHORT" in exit_reason else "SHORT"
                                
                                # ── 趨勢空間過濾 (Trend Space Filter) ──
                                last_k = channel_df.iloc[-1]
                                kc_mid = float(last_k.get("kc_middle") or last_k.get("ema_20") or channel_price)
                                
                                # 空單平倉(準備換多) 但價格仍在 KC 中軌下方 -> 屏蔽做多
                                if relay_dir == "LONG" and channel_price < kc_mid:
                                    engine.account.log(
                                        f"🛑 [趨勢空間過濾] {symbol} 平空單但價格 ({channel_price:.4f}) 仍在 KC 中軌 ({kc_mid:.4f}) 下方，屏蔽 LONG 接力票據",
                                        "WARNING"
                                    )
                                # 多單平倉(準備換空) 但價格仍在 KC 中軌上方 -> 屏蔽做空
                                elif relay_dir == "SHORT" and channel_price > kc_mid:
                                    engine.account.log(
                                        f"🛑 [趨勢空間過濾] {symbol} 平多單但價格 ({channel_price:.4f}) 仍在 KC 中軌 ({kc_mid:.4f}) 上方，屏蔽 SHORT 接力票據",
                                        "WARNING"
                                    )
                                else:
                                    if not hasattr(engine, "_trend_relay_watch"):
                                        engine._trend_relay_watch = {}
                                    engine._trend_relay_watch[symbol] = {
                                        "direction": relay_dir,
                                        "exit_bar_id": current_bar_id,
                                        "touched_structure": False,
                                        "relay_phase": "WAITING",
                                    }
                                    engine.account.log(
                                        f"📡 [趨勢接力備戰] {symbol} 等待回調到KC中軌/MA15，確認{relay_dir}接力進場（最多10根K）",
                                        "INFO"
                                    )
                        else:
                            engine.account.log(
                                f"🛑 [拒絕接力] {symbol} [{exit_reason}] 平倉時淨利 ({current_profit_usdt:.2f}U) 不足 2U，不觸發換手接力",
                                "WARNING"
                            )

                    engine.account.log(f"✅ [狀態重置] {symbol} 平倉完成，已清空歷史狀態，次根 K 棒恢復掃描", "SUCCESS")
                return signal_progress, detected_candidates

        # IDLE 狀態 (空倉掃描)
        else:
            # 防重複開倉冷卻 (平倉後必須至少等待 3 根 K 線的呼吸空間)
            if last_exit_bar is not None:
                ticket_bar_id = getattr(engine, "_direct_reverse_ticket", {}).get(symbol, 0)
                has_reverse_ticket = (current_bar_id - ticket_bar_id <= 60 * 1000) if ticket_bar_id > 0 else False
                
                if not has_reverse_ticket:
                    if current_bar_id - last_exit_bar < 3 * 60 * 1000:  # 1m K線, 3根 = 3分鐘
                        return signal_progress, detected_candidates
                else:
                    if getattr(engine, "_direct_reverse_ticket_logged", {}).get(symbol) != ticket_bar_id:
                        engine.account.log(f"🔄 [換手接力] {symbol} 啟動動能接力，跳過冷卻期，立即評估對向進場", "INFO")
                        if not hasattr(engine, "_direct_reverse_ticket_logged"):
                            engine._direct_reverse_ticket_logged = {}
                        engine._direct_reverse_ticket_logged[symbol] = ticket_bar_id
                
            # ── 趨勢接力守門員 ────────────────────────────────────────────
            relay_watch = getattr(engine, "_trend_relay_watch", {}).get(symbol)
            relay_entry_forced = False
            relay_direction    = None

            if relay_watch:
                relay_dir   = relay_watch["direction"]
                exit_bar_id = relay_watch["exit_bar_id"]
                relay_phase = relay_watch["relay_phase"]

                # 超過 10 根 K (10 分鐘) → 清除，回到一般掃描
                if current_bar_id - exit_bar_id > 10 * 60 * 1000:
                    engine.account.log(f"⏰ [接力逾時] {symbol} 10根K內未完成接力，清除觀察狀態", "INFO")
                    engine._trend_relay_watch.pop(symbol, None)
                    relay_watch = None
                else:
                    last_k = channel_df.iloc[-1]
                    kc_mid = float(last_k.get("kc_middle") or last_k.get("ema_20") or channel_price)
                    ma15   = float(last_k.get("ma15") or kc_mid)
                    atr_v  = float(last_k.get("atr") or channel_price * 0.001)

                    # Phase WAITING → TOUCHED：等待現價觸碰 KC中軌 或 MA15
                    if relay_phase == "WAITING":
                        if relay_dir == "LONG":
                            touched = channel_price <= kc_mid + atr_v or channel_price <= ma15 + atr_v
                        else:
                            touched = channel_price >= kc_mid - atr_v or channel_price >= ma15 - atr_v
                        if touched:
                            relay_watch["touched_structure"] = True
                            relay_watch["relay_phase"] = "TOUCHED"
                            relay_phase = "TOUCHED"
                            engine.account.log(f"📍 [接力觸碰] {symbol} 已觸碰KC中軌/MA15，等待 {relay_dir} 確認K", "INFO")

                    # Phase TOUCHED → CONFIRMED：確認K（實體比 >= 0.4 + 成交量 >= 1.2x）
                    if relay_phase == "TOUCHED" and len(channel_df) >= 3:
                        ck        = channel_df.iloc[-2]
                        ck_open   = float(ck["open"])
                        ck_close  = float(ck["close"])
                        ck_high   = float(ck["high"])
                        ck_low    = float(ck["low"])
                        ck_vol    = float(ck.get("volume", 0) or 0)
                        ck_range  = ck_high - ck_low
                        ck_body   = abs(ck_close - ck_open)
                        body_ratio = ck_body / ck_range if ck_range > 0 else 0

                        try:
                            vols    = [float(channel_df.iloc[i].get("volume", 0) or 0) for i in range(-7, -2)]
                            vol_avg = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
                        except Exception:
                            vol_avg = 0

                        is_confirm = (
                            (relay_dir == "LONG"  and ck_close > ck_open and body_ratio >= 0.4) or
                            (relay_dir == "SHORT" and ck_close < ck_open and body_ratio >= 0.4)
                        )
                        vol_ok = vol_avg <= 0 or ck_vol >= 1.2 * vol_avg

                        if is_confirm and vol_ok:
                            relay_entry_forced = True
                            relay_direction    = relay_dir
                            engine.account.log(
                                f"🚀 [接力確認] {symbol} {relay_dir} 確認K出現（實體={body_ratio:.2f}，量OK={vol_ok}），立即接力進場",
                                "SUCCESS"
                            )
                            engine._trend_relay_watch.pop(symbol, None)

            # ── 統一進場策略評估 ──────────────────────────────────────────
            entry_strategy = UnifiedEntryStrategy()
            
            from core.engine import market_crash_entries_paused
            is_system_halted = market_crash_entries_paused(getattr(engine, "_market_crash_entry_cooldown_until", 0.0), time.time())
            if is_system_halted:
                return signal_progress, detected_candidates
                
            print(f"[UnifiedEntry] Evaluating {symbol} at {channel_price:.4f} (Bar ID: {current_bar_id})", flush=True)
            
            # 診斷日誌：印出前一根K棒的實體/ATR比例，讓您清楚看到是否接近特例K門檻
            try:
                _prev = channel_df.iloc[-2]
                _atr = float(_prev.get("atr", 0))
                _body = abs(float(_prev["close"]) - float(_prev["open"]))
                _ratio = _body / _atr if _atr > 0 else 0
                if _ratio >= 1.5:  # 只在接近門檻時才印，避免日誌太吵
                    _tag = "🔥 SPECIAL_ENTRY_ELIGIBLE" if _ratio >= 2.0 else f"⚠️ NEAR_SPECIAL_ENTRY"
                    print(f"[UnifiedEntry] {symbol} prev bar body/ATR = {_ratio:.2f}x  {_tag}", flush=True)
            except Exception:
                pass
            
            velocity_drop_ratio = engine.get_velocity_drop_ratio(symbol)
            
            # 接力強制方向（選項B寬鬆：高分UnifiedEntry仍可進場，接力方向優先）
            sides_to_try = (relay_direction, ) if relay_entry_forced else ("LONG", "SHORT")
            
            for direct_side in sides_to_try:
                allowed, reason, entry_decision = entry_strategy.evaluate_entry(
                    channel_df, channel_price, direct_side, velocity_drop_ratio=velocity_drop_ratio,
                    relay_forced=relay_entry_forced
                )
                
                # 接力確認情況：若一般入場被拒，仍允許接力（繞過 UnifiedEntry 篩選）
                if relay_entry_forced and not allowed:
                    engine.account.log(f"🔀 [接力強制] {symbol} {direct_side} UnifiedEntry 拒絕但接力條件已確認，強制進場", "INFO")
                    allowed = True
                    reason  = f"TREND_RELAY_{direct_side}"
                    entry_decision = {"action": "ENTER"}
                elif not allowed or entry_decision.get("action") != "ENTER":
                    print(f"[{symbol}] {direct_side} Rejected: {reason}", flush=True)
                    if reason == "WAIT_PROFIT_SPACE_TOO_SMALL":
                        engine.account.log(f"[Skip Order] 預期獲利空間不足 ({symbol} {direct_side})，跳過開倉。", "INFO")
                    # 選項B：非接力期間若是高分也可進兩方
                    if not relay_entry_forced and relay_watch and relay_watch.get("relay_phase") != "CONFIRMED":
                        continue   # 接力觀察中且未確認 → 跳過（等回調）
                    continue

                if "[SPECIAL_ENTRY] Extreme Impulse" in reason:
                    engine.account.log(f"⚡ [Special Entry] Extreme Momentum Triggered (2.0+ ATR) - Bypass Filters. ({symbol} {direct_side})", "INFO")
                elif "Trend-Aligned MA Cross" in reason:
                    engine.account.log(f"📉 [Reversal Entry] {symbol} MA5 交叉 MA15 + 順應大級別斜率，結構性反轉進場 ({direct_side})", "INFO")
                elif "Trend Continuation" in reason:
                    engine.account.log(f"🔥 [Entry] Trend Continuation — {symbol} 趨勢已啟動，動態空間門檻通過，果斷上車 ({direct_side})", "INFO")
                elif "Initial Breakout" in reason:
                    engine.account.log(f"🚀 [Entry] Initial Breakout — {symbol} 初始破軌動能充足，進場 ({direct_side})", "INFO")
                else:
                    engine.account.log(f"🚀 [進場觸發] {symbol} 滿足進場條件: {reason} ({direct_side})", "INFO")
                
                # Track D (趨勢延續) 使用 50% 倉位，盈虧比較低，以頻率彌補
                is_track_d = reason.startswith("TRACK_D_")
                size_fraction = 0.5 if is_track_d else 1.0
                if is_track_d:
                    engine.account.log(
                        f"📈 [趨勢延續單] {symbol} Track D 觸發，自動套用 50% 基礎倉位（緩步順勢策略）",
                        "INFO"
                    )
                
                await engine._execute_confirmed_channel_break(
                    symbol, channel_df, channel_price, direct_side, daily_halt,
                    v8_reason=reason, size_fraction=size_fraction
                )
                break
            
        return signal_progress, detected_candidates

    except Exception as e:
        engine.account.log(f'⚠️ [{symbol}] 處理失敗: {e}', 'WARNING')
        import traceback
        traceback.print_exc()
    return signal_progress, detected_candidates
