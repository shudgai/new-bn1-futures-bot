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

    reason = None

    # ══════════════════════════════════════════════════════════════════
    # 【P1 — 頂部賣壓瞬間秒平】大實體反向 K / 真實吞沒 (任何持倉均適用)
    #   ·多單：陰線實體 >= 0.8 ATR，或完全吞沒前一根陽線
    #   ·空單：陽線實體 >= 0.8 ATR，或完全吞沒前一根陰線
    # ══════════════════════════════════════════════════════════════════
    if not reason and len(closed) >= 2:
        c_body_len  = abs(close - c_open)
        c1_body_len = abs(float(c1.close) - float(c1.open))

        if sign == 1:   # 多單：偵測頂部反轉大陰線
            bear_body = c_open - close
            if bear_body >= 0.8 * atr:
                reason = 'EXIT_LONG_SELLING_PRESSURE_BIG_BEAR'
            elif (close < c_open                              # 陰線
                  and c_body_len >= c1_body_len * 0.9        # 吞沒前根實體
                  and c1.close > c1.open                     # 前根是陽線
                  and close < float(c1.open)):               # 收盤壓穿前根開盤
                reason = 'EXIT_LONG_SELLING_PRESSURE_ENGULF'

        elif sign == -1:  # 空單：偵測底部暴拉大陽線
            bull_body = close - c_open
            if bull_body >= 0.8 * atr:
                reason = 'EXIT_SHORT_BUYING_PRESSURE_BIG_BULL'
            elif (close > c_open
                  and c_body_len >= c1_body_len * 0.9
                  and c1.close < c1.open
                  and close > float(c1.open)):
                reason = 'EXIT_SHORT_BUYING_PRESSURE_ENGULF'

    # ══════════════════════════════════════════════════════════════════
    # 【P2 — 外軌 MA3 轉向 + 連續小 K (冷水煮青蛙 / 耗竭)】
    #   前提：持倉曾衝出外軌 (has_broken_outer_band == True)
    # ══════════════════════════════════════════════════════════════════
    # 狀態追蹤：記錄是否曾衝出外軌
    if not position.get('has_broken_outer_band'):
        opened_ts = position.get('open_timestamp')
        if opened_ts:
            trade_candles = closed[closed['timestamp'].astype(float) >= float(opened_ts) * 1000 - 60000]
            if sign == 1 and (trade_candles['high'].astype(float) > trade_candles['kc_upper'].astype(float)).any():
                position['has_broken_outer_band'] = True
            elif sign == -1 and (trade_candles['low'].astype(float) < trade_candles['kc_lower'].astype(float)).any():
                position['has_broken_outer_band'] = True
    has_broken_outer = position.get('has_broken_outer_band', False)

    if not reason and has_broken_outer and len(closed) >= 3:
        ma3_curr    = float(c.ma3)
        ma3_prev    = float(c1.ma3)
        c_body_len  = abs(close - c_open)
        c1_body_len = abs(float(c1.close) - float(c1.open))
        is_doji     = c_body_len <= 0.25 * atr

        opened_ts2   = float(position.get('open_timestamp') or 0)
        trade_candles2 = closed[closed['timestamp'].astype(float) >= opened_ts2 * 1000 - 60000]

        # ── 外軌耗竭：MA3 走平/轉向 + 滯漲形態 ───────────────────────
        if sign == 1:
            is_super_trend   = (ma3_curr > float(c.kc_upper)) and (ma3_curr > ma3_prev)
            long_upper_wick  = (c_high - max(close, c_open)) > c_body_len * 2
            is_engulfing_ex  = (close < c_open) and (c_body_len >= 0.8 * atr)
            is_stalled       = is_doji or long_upper_wick
            ma3_stalled      = (ma3_curr < ma3_prev) and (close < c_open or long_upper_wick)
            max_high         = float(trade_candles2['high'].max()) if not trade_candles2.empty else c_high
            not_new_high     = (c_high < max_high and float(c1.high) < max_high)
            
            if is_engulfing_ex:
                reason = 'EXIT_LONG_REVERSE_ENGULFING_0.8ATR'
            elif not is_super_trend and (is_stalled or ma3_stalled or not_new_high):
                reason = 'EXIT_LONG_OUTER_BAND_EXHAUSTION'
        elif sign == -1:
            is_super_trend   = (ma3_curr < float(c.kc_lower)) and (ma3_curr < ma3_prev)
            long_lower_wick  = (min(close, c_open) - c_low) > c_body_len * 2
            is_engulfing_ex  = (close > c_open) and (c_body_len >= 0.8 * atr)
            is_stalled       = is_doji or long_lower_wick
            ma3_stalled      = (ma3_curr > ma3_prev) and (close > c_open or long_lower_wick)
            min_low          = float(trade_candles2['low'].min()) if not trade_candles2.empty else c_low
            not_new_low      = (c_low > min_low and float(c1.low) > min_low)
            
            if is_engulfing_ex:
                reason = 'EXIT_SHORT_REVERSE_ENGULFING_0.8ATR'
            elif not is_super_trend and (is_stalled or ma3_stalled or not_new_low):
                reason = 'EXIT_SHORT_OUTER_BAND_EXHAUSTION'

    # ── 連續小碎步出血 (需 >= 5 根歷史) ──────────────────────────────
    if not reason and has_broken_outer and len(closed) >= 5:
        last3 = closed.iloc[-3:]
        c2, c1b, cb = last3.iloc[0], last3.iloc[1], last3.iloc[2]

        if sign == 1:
            is_super_trend = (float(cb.ma3) > float(cb.kc_upper)) and (float(cb.ma3) > float(c1b.ma3))
            three_bear       = all(float(r.close) < float(r.open) and float(r.close) < float(r.ma3) for _, r in last3.iterrows())
            descending_close = float(cb.close) < float(c1b.close) < float(c2.close)
            ma3_flat_down    = (float(cb.ma3) <= float(c1b.ma3) and float(c1b.ma3) <= float(c2.ma3) and float(cb.close) < float(cb.ma3))
            if not is_super_trend and (three_bear or descending_close or ma3_flat_down):
                reason = 'CONSECUTIVE_BLEED_EXIT_LONG'
        elif sign == -1:
            is_super_trend = (float(cb.ma3) < float(cb.kc_lower)) and (float(cb.ma3) < float(c1b.ma3))
            three_bull       = all(float(r.close) > float(r.open) and float(r.close) > float(r.ma3) for _, r in last3.iterrows())
            ascending_close  = float(cb.close) > float(c1b.close) > float(c2.close)
            ma3_flat_up      = (float(cb.ma3) >= float(c1b.ma3) and float(c1b.ma3) >= float(c2.ma3) and float(cb.close) > float(cb.ma3))
            if not is_super_trend and (three_bull or ascending_close or ma3_flat_up):
                reason = 'CONSECUTIVE_BLEED_EXIT_SHORT'

    # ══════════════════════════════════════════════════════════════════
    # 【P3 — 縮回通道內，趨勢破位兜底 (MA15 + KC 中軌)】
    # ══════════════════════════════════════════════════════════════════
    if not reason:
        ma15 = float(c.get('ma15', c.get('ma3', close))) if hasattr(c, 'get') else float(getattr(c, 'ma15', getattr(c, 'ma3', close)))
        if sign == 1 and close < ma15:
            reason = 'EXIT_LONG_BELOW_MA15_LIFELINE'
        elif sign == -1 and close > ma15:
            reason = 'EXIT_SHORT_ABOVE_MA15_LIFELINE'

    if not reason:
        price_broken   = sign * (close - kc_mid) < 0
        trend_reversed = sign * (kc_mid - prev_kc_mid) < 0
        if price_broken or trend_reversed:
            reason = 'EXIT_KC_MIDDLE_DEFENSE_CLOSED'

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
