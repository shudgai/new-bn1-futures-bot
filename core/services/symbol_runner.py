"""Live second-bar entry and confirmed exits, serialized by the symbol lock."""
import copy
import math
from core.services.strategies.unified_entry_strategy import confirmed
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
from core.services.candle_data import log_entry_gate, entry_frame_evidence


async def process_single_symbol_runner(engine, symbol, now_time, btc_1m_turn, daily_halt,
                                      exit_frame=None, exit_quote=None, exit_only=False):
    frame = exit_frame
    if frame is None:
        frame = await engine.fetch_klines(symbol,timeframe='1m',limit=200,keep_live=True)
        if frame is not None and not frame.empty:
            frame = engine.strategy.compute_indicators(frame.copy())
    closed = confirmed(frame)
    if closed is None:
        return [], []
    cache = getattr(engine,'_channel_exit_frames',None)
    if cache is None:
        cache = engine._channel_exit_frames = {}
    cache[symbol] = frame.copy()
    quote = float(exit_quote if exit_quote is not None else
                  (getattr(engine,'tickers',{}).get(symbol) or frame.iloc[-1]['close']))
    if not math.isfinite(quote) or quote <= 0:
        return [], []
    position = engine.account.positions.get(symbol)
    preferred = None
    if position:
        engine._take_over_manual_position(symbol,position)
        
        from core.services.exits.hard_stop_service import enforce_hard_stop
        if await enforce_hard_stop(engine.account, symbol, quote):
            return [], []

        # =========================================================
        # 唯一三大鐵律物理鎖 (一票否決制)
        # =========================================================
        def verify_exit_three_rules_strict(position, quote, closed, frame):
            position_side = position['side']
            indicators = closed.iloc[-1].to_dict()
            kc_middle = float(indicators['kc_middle'])
            atr = float(indicators.get('atr', 0.0001))
            
            # -------------------------------------------------------------
            # 鐵律 1：盤中即時貫穿中軌（Tick 級極速逃命）
            # -------------------------------------------------------------
            if position_side == "LONG" and quote <= kc_middle:
                return f"鐵律1觸發：多單盤中跌穿 KC 中軌 ({quote} <= {kc_middle})，逃命平倉！"
            
            if position_side == "SHORT" and quote >= kc_middle:
                return f"鐵律1觸發：空單盤中突破 KC 中軌 ({quote} >= {kc_middle})，逃命平倉！"

            # -------------------------------------------------------------
            # 鐵律 2：階梯式追蹤止盈 (Tiered Trailing Stop)
            # -------------------------------------------------------------
            entry = float(position['entry_price'])
            qty = float(position['quantity'])
            sign = 1 if position_side == "LONG" else -1
            current_pnl_u = sign * (quote - entry) * qty
            
            max_pnl_u = max(float(position.get('peak_pnl_usd', 0.0)), current_pnl_u)
            position['peak_pnl_usd'] = max_pnl_u # Update peak tracking
            
            # 使用當前的 1M ATR 計算動態波動 (避免短期雜訊)
            max_pnl_atr = max_pnl_u / (atr * qty) if (atr * qty) > 0 else 0.0
            
            if max_pnl_u > 0:
                drawdown_pct = (max_pnl_u - current_pnl_u) / max_pnl_u
                
                # 第二階段（強鎖利）：獲利達 2.5 ATR 或 10U
                if (max_pnl_atr >= 2.5) or (max_pnl_u >= 10.0):
                    if drawdown_pct >= 0.25:
                        return f"鐵律2觸發(二階強鎖利)：大肉浮盈 (峰值 {max_pnl_u:.2f}U / {max_pnl_atr:.1f}ATR) 回吐達 {drawdown_pct*100:.1f}% >= 25%，鎖定波段利潤秒平！"
                        
                # 第一階段（弱鎖利/保本）：獲利達 1.5 ATR 或 5U
                elif (max_pnl_atr >= 1.5) or (max_pnl_u >= 5.0):
                    if drawdown_pct >= 0.40:
                        return f"鐵律2觸發(一階弱鎖利)：初段浮盈 (峰值 {max_pnl_u:.2f}U / {max_pnl_atr:.1f}ATR) 回吐達 {drawdown_pct*100:.1f}% >= 40%，提早鎖利平倉！"
                    # 保本底線：既然達標一階，絕不允許帳面轉虧
                    if current_pnl_u <= min(0.5, max_pnl_u * 0.1): 
                        return f"鐵律2觸發(一階保本防線)：初段浮盈即將歸零，強制保本平倉！"

            # -------------------------------------------------------------
            # 鐵律 3：1M 收盤實質突破真峰頂/谷底（波段結構確認死亡，嚴格等收盤）
            # -------------------------------------------------------------
            prev_close = float(indicators['close']) # indicators are from closed bars
            
            # 尋找最近的 Swing High/Low (滾動 15 根極值做為真峰頂/谷底)
            # 或者使用已確認的 swing (如果指標有提供，這裡自己算最保險)
            if len(closed) >= 15:
                last_15 = closed.iloc[-15:]
                swing_high = float(last_15['high'].max())
                swing_low = float(last_15['low'].min())
            else:
                swing_high = float(closed['high'].max())
                swing_low = float(closed['low'].min())

            if position_side == "LONG" and prev_close < swing_low:
                return f"鐵律3觸發：多單 1M 收盤價 ({prev_close}) 實質跌破真谷底 ({swing_low})，結構宣告死亡平多！"
                
            if position_side == "SHORT" and prev_close > swing_high:
                return f"鐵律3觸發：空單 1M 收盤價 ({prev_close}) 實質突破真峰頂 ({swing_high})，結構宣告死亡平空！"

            # 未觸發三大鐵律，拒絕平倉
            return None

        exit_reason = verify_exit_three_rules_strict(position, quote, closed, frame)

        if exit_reason:
            position['closed_exit_state'] = dict(policy='THREE_STRICT_RULES', pending=True, reason=exit_reason)
        state = position.get('closed_exit_state')
        if state is not None and engine.account.position_meta.setdefault(symbol, {}).get('closed_exit_state') != state:
            engine.account.position_meta[symbol]['closed_exit_state'] = copy.deepcopy(state)
            engine.account.save_state()

        if not exit_reason:
            return [], []
            
        old_side = position['side']
        # ── 全倉平倉 ─────────────────        # ── 全倉平倉（第三階段各種出場訊號）─────────────────
        filled = await engine.account.close_position(symbol, quote, 'Closed1M ' + exit_reason, is_manual=True)
        if not filled or symbol in engine.account.positions:
            return [], []  # pending state is persisted and retried
        engine.account.position_meta.pop(symbol, None)
        for name in ('_channel_outer_reentry_after_exit', '_channel_pending_reverse_bar', '_closed_bear_reverse_tickets'):
            getattr(engine, name, {}).pop(symbol, None)
        getattr(engine.account, 'channel_profit_reentries', {}).pop(symbol, None)
        engine.account.save_state()
        return [], []  # Closing never initiates an entry in the same lifecycle pass.
    elif exit_only:
        return [], []
    if daily_halt:
        return [], []
    from core.services.strategies.pure_trend_v2 import successful_exit_ticket
    ticket = successful_exit_ticket(engine.account, symbol)
    if ticket and int(float(frame.iloc[-1]['timestamp']) // 60000) - ticket['exit_bar_index'] < 5:
        for side in ('LONG', 'SHORT'):
            log_entry_gate(engine, symbol, side, 'CLOSED_SIGNAL', 'WAIT_POST_EXIT_5_BAR_COOLDOWN', float(frame.iloc[-1]['timestamp']))
        return [], []
    # New entries are independently evaluated against the whitelist.
    sides = ('LONG', 'SHORT')
    candidates = []
    for side in sides:
        # ── BTC 聯動熔斷審查 (BTC Market Circuit Breaker) ──
        if btc_1m_turn == "SHORT" and side == "LONG":
            reason = "BLOCKED_BTC_DUMPING_FORBID_LONG"
            log_entry_gate(engine, symbol, side, 'CLOSED_SIGNAL', reason, float(closed.iloc[-1].timestamp))
            continue
        if btc_1m_turn == "LONG" and side == "SHORT":
            reason = "BLOCKED_BTC_PUMPING_FORBID_SHORT"
            log_entry_gate(engine, symbol, side, 'CLOSED_SIGNAL', reason, float(closed.iloc[-1].timestamp))
            continue
            
        from core.services.strategies.pure_trend_v2 import evaluate_v2_frame
        decision = evaluate_v2_frame(frame, quote, account=engine.account, symbol=symbol)

        if decision and decision['side'] == side:
            decision['rule'] = decision['type']
            log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',decision['reason'],decision['confirmation_bar_id'], snapshot=entry_frame_evidence(frame))
            candidates.append(decision)
        else:
            log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL','WAIT_PURE_TREND_V2',float(frame.iloc[-1].timestamp), snapshot=entry_frame_evidence(frame))

    if candidates:
        decision = min(candidates,key=lambda d:d['rule'])
        await engine._execute_confirmed_channel_break(symbol,frame,quote,decision['side'],
                                                      daily_halt,v8_reason=decision['type'],
                                                      candidate_bar_id=decision['confirmation_bar_id'])
    return [], []
