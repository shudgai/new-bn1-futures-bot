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

    reason = None
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
                if math.isfinite(quote) and quote > 0 and sign*(quote-stop_loss) <= 0:
                    reason = 'EXIT_INITIAL_ATR_HARD_STOP'
            
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
        position['cooldown_mode'] = 'NONE'
