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
    """Three-tier exit: P1=instant engulf, P2=outer-band bleed, P3=MA15/KC-mid break."""
    sign = 1 if position['side'] == 'LONG' else -1

    c  = closed.iloc[-1]
    c1 = closed.iloc[-2]

    close  = float(c.close)
    c_open = float(c.open)
    c_high = float(c.high)
    c_low  = float(c.low)
    kc_mid      = float(c.kc_middle)
    prev_kc_mid = float(c1.kc_middle)

    # =======================================================
    # 【多單 (LONG) 出場標準】防範「賣壓」
    # =======================================================
    if sign == 1:
        # 1. 當根收盤出現【大實體陰線】（c_close < c_open 且 實體 >= 0.8 ATR）
        if close < c_open and abs(close - c_open) >= 0.8 * atr:
            return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_LONG_SELLING_PRESSURE_BIG_BEAR')
            
        # 2. 當根收盤【跌破 MA15】（c_close < ma15）
        ma15 = float(c.get('ma15', c.get('ma3', close))) if hasattr(c, 'get') else float(getattr(c, 'ma15', getattr(c, 'ma3', close)))
        if close < ma15:
            return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_LONG_BELOW_MA15_LIFELINE')
            
        # 3. 外軌滯漲：MA3 轉平/下彎 且 連續 3 根陰線
        if len(closed) >= 3:
            ma3_curr = float(c.ma3)
            ma3_prev = float(c1.ma3)
            last3 = closed.iloc[-3:]
            three_bear = all(float(r.close) < float(r.open) for _, r in last3.iterrows())
            if ma3_curr <= ma3_prev and three_bear:
                return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_LONG_MA3_DOWN_3_BEAR')

    # =======================================================
    # 【空單 (SHORT) 出場標準】防範「買盤反撲」
    # =======================================================
    elif sign == -1:
        # 1. 當根收盤出現【大實體陽線】（c_close > c_open 且 實體 >= 0.8 ATR）
        if close > c_open and abs(close - c_open) >= 0.8 * atr:
            return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_SHORT_BUYING_PRESSURE_BIG_BULL')
            
        # 2. 當根收盤【漲破 MA15】（c_close > ma15）
        ma15 = float(c.get('ma15', c.get('ma3', close))) if hasattr(c, 'get') else float(getattr(c, 'ma15', getattr(c, 'ma3', close)))
        if close > ma15:
            return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_SHORT_ABOVE_MA15_LIFELINE')
            
        # 3. 外軌滯跌：MA3 轉平/上翹 且 連續 3 根陽線
        if len(closed) >= 3:
            ma3_curr = float(c.ma3)
            ma3_prev = float(c1.ma3)
            last3 = closed.iloc[-3:]
            three_bull = all(float(r.close) > float(r.open) for _, r in last3.iterrows())
            if ma3_curr >= ma3_prev and three_bull:
                return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_SHORT_MA3_UP_3_BULL')

    # 其餘情況一律抱緊讓利潤奔跑！
    return dict(should_exit=False, action='HOLD', reason='TREND_RUNNING')



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
