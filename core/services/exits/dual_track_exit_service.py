"""Completed-candle MA15 defense and entry breakeven."""
import math
from core.interfaces.exit_interface import IExitStrategy
from core.services.strategies.unified_entry_strategy import confirmed

POLICY = 'closed_1m_ma15_structure_v1'
SL_INIT_MULT = 1.5
DUAL_TRACK_STATE_KEYS = [
    'closed_exit_state', 'sl', 'tp', 'stop_loss', 'entry_atr', 'atr_sl',
    'atr_tp', 'atr_protection_version', 'swing_breakeven_armed', 'swing_peak_profit_atr',
    'swing_trailing_armed', 'swing_trailing_line', 'swing_trailing_last_bar',
    'has_broken_outer_band', 'peak_pnl_usdt', 'peak_profit_diff',
]


def evaluate_trend_exit_and_take_profit(position, closed, atr):
    """Exit only on a structural break through KC Middle or middle slope turning opposite, 
    plus extreme abnormal engulfing candle exits; ignore MA3/MA15."""
    sign = 1 if position['side'] == 'LONG' else -1
    
    c = closed.iloc[-1]
    c1 = closed.iloc[-2]
    
    close = float(c.close)
    c_open = float(c.open)
    c_high = float(c.high)
    c_low = float(c.low)
    
    kc_mid = float(c.kc_middle)
    prev_kc_mid = float(c1.kc_middle)
    
    price_broken = sign * (close - kc_mid) < 0
    trend_reversed = sign * (kc_mid - prev_kc_mid) < 0

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # P1【最高優先級】跌破 MA15 生命線：獨立觸發，不等任何計數器
    # 多單：close < ma15 → 趨勢防線潰敗，當根收線第 0 秒立即平倉
    # 空單：close > ma15 → 空頭結構瓦解，當根收線第 0 秒立即平倉
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ma15 = float(c.get('ma15', c.get('ma3', close))) if hasattr(c, 'get') else float(getattr(c, 'ma15', getattr(c, 'ma3', close)))
    if sign == 1 and close < ma15:
        reason = 'EXIT_LONG_BELOW_MA15_LIFELINE'
    elif sign == -1 and close > ma15:
        reason = 'EXIT_SHORT_ABOVE_MA15_LIFELINE'

    # P2: KC 中軌防守（MA15 未破時才評估）
    if not reason:
        price_broken = sign * (close - kc_mid) < 0
        trend_reversed = sign * (kc_mid - prev_kc_mid) < 0
        if price_broken or trend_reversed:
            reason = 'EXIT_KC_MIDDLE_DEFENSE_CLOSED'
    
    # 極端異常 K 線緊急出場 (Abnormal Engulfing / V-Reversal)
    if not reason and len(closed) >= 3:
        if sign == -1:  # 空單持倉，偵測底部異常暴拉大陽線
            c_body = close - c_open
            # 條件 1: 實體大陽線 >= 1.5 ATR
            if c_body >= 1.5 * atr:
                reason = 'EXIT_SHORT_ABNORMAL_BULL_CLOSED'
            else:
                # 條件 2: 實體完全吞沒前 2 根陰線的最高價與開盤價
                prev_2 = closed.iloc[-3:-1]
                if all((float(row.close) < float(row.open)) for _, row in prev_2.iterrows()):
                    max_prev_high_open = max(float(prev_2['high'].max()), float(prev_2['open'].max()))
                    if close > max_prev_high_open and c_open <= float(prev_2['close'].min()):
                        reason = 'EXIT_SHORT_ABNORMAL_BULL_CLOSED'
                        
        elif sign == 1:  # 多單持倉，偵測頂部異常暴跌大陰線
            c_body = c_open - close
            # 條件 1: 實體大陰線 >= 1.5 ATR
            if c_body >= 1.5 * atr:
                reason = 'EXIT_LONG_ABNORMAL_BEAR_CLOSED'
            else:
                # 條件 2: 實體完全吞沒前 2 根陽線的最低價與開盤價
                prev_2 = closed.iloc[-3:-1]
                if all((float(row.close) > float(row.open)) for _, row in prev_2.iterrows()):
                    min_prev_low_open = min(float(prev_2['low'].min()), float(prev_2['open'].min()))
                    if close < min_prev_low_open and c_open >= float(prev_2['close'].max()):
                        reason = 'EXIT_LONG_ABNORMAL_BEAR_CLOSED'

    # 狀態追蹤：記錄是否曾衝出外軌 (破軌加速段)
    if not position.get('has_broken_outer_band'):
        opened_ts = position.get('open_timestamp')
        if opened_ts:
            # Include the entry candle itself (-60000)
            trade_candles = closed[closed['timestamp'].astype(float) >= float(opened_ts) * 1000 - 60000]
            if sign == 1 and (trade_candles['high'].astype(float) > trade_candles['kc_upper'].astype(float)).any():
                position['has_broken_outer_band'] = True
            elif sign == -1 and (trade_candles['low'].astype(float) < trade_candles['kc_lower'].astype(float)).any():
                position['has_broken_outer_band'] = True
            
    has_broken_outer = position.get('has_broken_outer_band', False)

    # 外軌末端補跌/加速噴發之十字星與 MA3 衰竭即時收割 (Exhaustion Mode)
    if not reason and has_broken_outer and len(closed) >= 3:
        entry_price = float(position['entry_price'])
        unrealized_profit = sign * (close - entry_price)
        unrealized_profit_pct = unrealized_profit / entry_price
        unrealized_pnl_usdt = float(position.get('unrealized_pnl') or 0.0)
        
        # 條件 2：當前未實現利潤 >= 1.0 * ATR 或 百分比 >= 3% 或 USDT >= 10
        if unrealized_profit >= 1.0 * atr or unrealized_profit_pct >= 0.03 or unrealized_pnl_usdt >= 10.0:
            ma3_curr = float(c.ma3)
            ma3_prev = float(c1.ma3)
            
            c_body_len = abs(close - c_open)
            c1_body_len = abs(float(c1.close) - float(c1.open))
            is_doji = c_body_len <= 0.25 * atr
            
            opened_ts = float(position.get('open_timestamp') or 0)
            trade_candles = closed[closed['timestamp'].astype(float) >= opened_ts * 1000 - 60000]
            
            if sign == -1:  # 空單
                long_lower_wick = (min(close, c_open) - c_low) > c_body_len * 2
                is_engulfing = (close > c_open) and (c_body_len >= c1_body_len or (close - c_open) >= 1.0 * atr)
                is_stalled = is_doji or long_lower_wick or is_engulfing
                ma3_turned_stalled = (ma3_curr > ma3_prev) and (close > c_open or long_lower_wick)
                
                min_low_overall = float(trade_candles['low'].min()) if not trade_candles.empty else c_low
                not_new_low = (c_low > min_low_overall and float(c1.low) > min_low_overall)
                
                if is_stalled or ma3_turned_stalled or not_new_low:
                    reason = 'EXIT_SHORT_OUTER_BAND_EXHAUSTION'
                    
            elif sign == 1:  # 多單
                long_upper_wick = (c_high - max(close, c_open)) > c_body_len * 2
                is_engulfing = (close < c_open) and (c_body_len >= c1_body_len or (c_open - close) >= 1.0 * atr)
                is_stalled = is_doji or long_upper_wick or is_engulfing
                ma3_turned_stalled = (ma3_curr < ma3_prev) and (close < c_open or long_upper_wick)
                
                max_high_overall = float(trade_candles['high'].max()) if not trade_candles.empty else c_high
                not_new_high = (c_high < max_high_overall and float(c1.high) < max_high_overall)
                
                if is_stalled or ma3_turned_stalled or not_new_high:
                    reason = 'EXIT_LONG_OUTER_BAND_EXHAUSTION'

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 冷水煮青蛙防禦：高位連續小碎步陰跌/陽推 (CONSECUTIVE_BLEED_EXIT)
    # 前提：曾衝出外軌 且 有足夠浮盈（避免低位雜訊誤平）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if not reason and has_broken_outer and len(closed) >= 5:
        entry_price_b = float(position['entry_price'])
        unrealized_b = sign * (close - entry_price_b)
        unrealized_usdt_b = float(position.get('unrealized_pnl') or 0.0)
        has_profit = unrealized_b >= 1.0 * atr or unrealized_usdt_b >= 8.0

        if has_profit:
            last3 = closed.iloc[-3:]
            c2, c1b, cb = last3.iloc[0], last3.iloc[1], last3.iloc[2]

            if sign == 1:  # 多單：偵測連續小陰線磨損
                # 條件 1：連續 3 根均為陰線且每根 close < 該根 ma3
                three_bear = all(
                    float(r.close) < float(r.open) and float(r.close) < float(r.ma3)
                    for _, r in last3.iterrows()
                )
                # 條件 2：收盤價連續走低
                descending_close = (
                    float(cb.close) < float(c1b.close) < float(c2.close)
                )
                # 條件 3：在外軌高位，MA3 連續 2 根走平/下彎 且最新 close < ma3
                ma3_flat_down = (
                    float(cb.ma3) <= float(c1b.ma3)
                    and float(c1b.ma3) <= float(c2.ma3)
                    and float(cb.close) < float(cb.ma3)
                )
                if three_bear or descending_close or ma3_flat_down:
                    reason = 'CONSECUTIVE_BLEED_EXIT_LONG'

            elif sign == -1:  # 空單：偵測連續小陽線緩推
                # 條件 1：連續 3 根均為陽線且每根 close > 該根 ma3
                three_bull = all(
                    float(r.close) > float(r.open) and float(r.close) > float(r.ma3)
                    for _, r in last3.iterrows()
                )
                # 條件 2：收盤價連續走高
                ascending_close = (
                    float(cb.close) > float(c1b.close) > float(c2.close)
                )
                # 條件 3：MA3 連續 2 根走平/上彎 且最新 close > ma3
                ma3_flat_up = (
                    float(cb.ma3) >= float(c1b.ma3)
                    and float(c1b.ma3) >= float(c2.ma3)
                    and float(cb.close) > float(cb.ma3)
                )
                if three_bull or ascending_close or ma3_flat_up:
                    reason = 'CONSECUTIVE_BLEED_EXIT_SHORT'

    if not reason:
        return dict(should_exit=False, action='HOLD', reason='TREND_RUNNING')
    return dict(should_exit=True, action='FULL_CLOSE', reason=reason)


class DualTrackExitStrategy(IExitStrategy):
    def initialize_position(self, position, entry_price, atr):
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(entry_atr=float(atr), sl=entry_price-sign*SL_INIT_MULT*atr,
                        stop_loss=entry_price-sign*SL_INIT_MULT*atr, tp=0.)

    def evaluate_exit(self, position, frame=None, current_price=None, **kwargs):
        if position.get('side') not in ('LONG', 'SHORT'):
            return None
        try:
            entry = float(position['entry_price'])
            opened = float(position.get('open_timestamp') or 0)*1000
            if not math.isfinite(entry) or entry <= 0 or not math.isfinite(opened):
                return None
            closed = confirmed(frame)
            atr = float(position.get('entry_atr') or
                        (closed.iloc[-2].atr if closed is not None else 0.))
            if not math.isfinite(atr) or atr <= 0:
                return None
            position['entry_atr'] = atr
            sign = 1 if position['side'] == 'LONG' else -1

            state = position.get('closed_exit_state') or {}
            if state.get('policy') == POLICY and state.get('reason') == 'EXIT_MA3_MA15_CROSS_CLOSED':
                position['closed_exit_state'] = dict(policy=POLICY, pending=False)
                state = position['closed_exit_state']
            if state.get('policy') == POLICY and state.get('pending'):
                return state['reason']

            reason = None
            stop_loss = float(position.get('stop_loss') or position.get('sl') or entry-sign*SL_INIT_MULT*atr)
            
            # Account quote updates must enforce protection
            if current_price is not None:
                quote = float(current_price)
                if math.isfinite(quote) and quote > 0:
                    if sign*(quote-stop_loss) <= 0:
                        reason = 'EXIT_INITIAL_ATR_HARD_STOP'
                    else:
                        current_pnl_usdt = float(position.get('unrealized_pnl') or 0.0)
                        peak_pnl = float(position.get('peak_pnl_usdt') or 0.0)
                        if current_pnl_usdt > peak_pnl:
                            position['peak_pnl_usdt'] = current_pnl_usdt
                            peak_pnl = current_pnl_usdt
                            
                        entry_price = float(position['entry_price'])
                        current_profit_diff = sign * (quote - entry_price)
                        peak_profit_diff = float(position.get('peak_profit_diff') or 0.0)
                        if current_profit_diff > peak_profit_diff:
                            position['peak_profit_diff'] = current_profit_diff
                            peak_profit_diff = current_profit_diff
                            
                        # 如果最高浮盈超過 10U 或是 1.0 ATR，強制啟用 20% 浮盈回撤保護 (保住80%)
                        if peak_pnl >= 10.0 and current_pnl_usdt <= peak_pnl * 0.80:
                            reason = 'INTRA_BAR_PEAK_DRAWDOWN_LOCK'
                        elif peak_profit_diff >= 1.0 * atr and current_profit_diff <= peak_profit_diff * 0.80:
                            reason = 'INTRA_BAR_PEAK_DRAWDOWN_LOCK'
            
            if reason is None and closed is not None:
                c1, c = closed.iloc[-2], closed.iloc[-1]
                if (float(c.timestamp)+60000 <= opened
                        or float(c.timestamp) <= float(position.get('channel_confirmation_bar_id') or -1)):
                    return None
                
                if sign*(float(c.close)-stop_loss) <= 0:
                    reason = 'EXIT_INITIAL_ATR_HARD_STOP'
                else:
                    # 「一股不賣」吃滿波段：關閉所有短線疲態與軌跡出場，只由 KC 中軌實體貫穿作為唯一出場依據
                    result = evaluate_trend_exit_and_take_profit(position, closed, atr)
                    reason = result['reason'] if result['should_exit'] else None
                    
            if reason:
                position['closed_exit_state'] = dict(policy=POLICY, pending=True, reason=reason)
            return reason
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position.pop('has_broken_outer_band', None)
        position['cooldown_mode'] = 'NONE'
