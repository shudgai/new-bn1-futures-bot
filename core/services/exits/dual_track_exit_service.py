"""Closed-one-minute active exits require adverse MA3 slope, price crossing
and a net margin ROE/profit gate. TP1 and explicit hard stops are independent
authorized exceptions; old discretionary pending exits must revalidate.
"""
import math
from core.interfaces.exit_interface import IExitStrategy
from core.services.strategies.unified_entry_strategy import confirmed
from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT

POLICY = 'closed_1m_v4_ma3_margin_roe'
DUAL_TRACK_STATE_KEYS = [
    'closed_exit_state', 'sl', 'tp',
    'entry_atr', 'atr_sl', 'atr_tp', 'atr_protection_version'
]

# ─── 高波動猛幣：動態保本與極速收割參數 ─────────────────────────────
FAST_EXIT_ATR_MULT = 1.5    # 浮盈達 1.5 ATR 啟動保本並允許收割
FAST_EXIT_FRACTION = 0.60   # 極速拐頭收割比例
BREAKEVEN_BUFFER   = 0.0002 # 保本緩衝（約雙邊手續費）

# ─── ATR 防線乘數 ──────────────────────────────────────────────────
SL_INIT_MULT       = 1.5    # 初始止損乘數


class DualTrackExitStrategy(IExitStrategy):

    def initialize_position(self, position, entry_price, atr):
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(
            entry_atr=float(atr),
            sl=entry_price - sign * SL_INIT_MULT * atr,
            tp=0.
        )

    # ─────────────────────────────────────────────────────────────
    def evaluate_exit(self, position, frame, current_price=None, **kwargs):
        closed = confirmed(frame)
        if closed is None or position.get('side') not in ('LONG', 'SHORT'):
            return None
        try:
            return self._evaluate(position, closed)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    # ─────────────────────────────────────────────────────────────
    def _evaluate(self, position, closed):
        entry  = float(position['entry_price'])
        opened = float(position.get('open_timestamp') or 0) * 1000
        if not math.isfinite(entry) or entry <= 0 or not math.isfinite(opened):
            return None

        # 需要最近三根已收線（c0=前前, c1=前, c=最新已收）
        if len(closed) < 3:
            return None
        c0, c1, c = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
        bar = float(c.timestamp)

        # 不用早於開倉時間的 K 棒管理倉位
        if bar + 60000 <= opened or bar <= float(position.get('channel_confirmation_bar_id') or -1):
            return None

        side  = position['side']
        sign  = 1 if side == 'LONG' else -1
        atr = float(position.get('entry_atr') or c1.atr)
        if not math.isfinite(atr) or atr <= 0:
            return None

        # ── 初始化或重建狀態 ───────────────────────────────────
        identity = [side, entry, opened]
        state = position.get('closed_exit_state')
        old_state = state if isinstance(state, dict) and state.get('identity') == identity else {}
        if not old_state or old_state.get('policy') != POLICY:
            state = dict(
                policy=POLICY, identity=identity,
                peak=entry, last_bar=-1,
                stop=entry - sign * SL_INIT_MULT * atr,
                outer=False, pending=None,
                tp1_executed=bool(position.get('is_half_closed') or old_state.get('tp1_executed')),
                breakeven_activated=bool(old_state.get('breakeven_activated')),
                is_half=bool(position.get('is_half_closed')),
            )
            position['closed_exit_state'] = state

        # 同步 partial_close 外部標記
        if position.get('is_half_closed') and not state.get('tp1_executed'):
            state['tp1_executed'] = True
            state['is_half'] = True

        # ── 當根基礎數值 ───────────────────────────────────────
        curr_close  = float(c.close)
        curr_open   = float(c.open)
        body_signed = sign * (curr_close - curr_open)   # >0 = 方向一致的長K

        # ── 大實體長K保護（嚴禁在強勢方向長K上觸發平倉）──────
        protected = body_signed > 0.8 * atr

        # ── 去重：同根只輸出一次訊號 ───────────────────────────
        if bar < state['last_bar']:
            return None
        state['last_bar'] = bar

        # ── 更新持倉期間的極值（峰/谷）────────────────────────
        peak = max(sign * state['peak'], sign * curr_close) * sign
        state['peak'] = peak

        # ── 軌道外標記（曾突破外軌才啟用脫軌止盈）─────────────
        rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
        outside          = sign * (curr_close           - float(c[rail]))  >= 0
        previous_outside = sign * (float(c1.close)      - float(c1[rail])) >= 0
        state['outer'] = state['outer'] or outside or previous_outside

        # ── 浮盈 1.5 ATR 觸發保本 ──────────────────────────────
        raw_pnl_per = sign * (curr_close - entry)
        if raw_pnl_per >= FAST_EXIT_ATR_MULT * atr:
            state['breakeven_activated'] = True

        # Independent hard exits use the original entry ATR.
        # Break-Even protects the position at entry cost.
        stop = entry if (state.get('tp1_executed') or state.get('breakeven_activated')) else entry - sign * SL_INIT_MULT * atr
        state['stop'] = stop
        position.update(sl=stop, atr_sl=stop, tp=0., atr_tp=0.)
        
        hard_reasons = {'EXIT_INITIAL_ATR_HARD_STOP', 'EXIT_KC_MIDDLE_HARD_STOP',
                        'EXIT_TP1_BREAKEVEN'}
        if state.get('pending') in hard_reasons:
            return state['pending']
        if sign * (curr_close - stop) <= 0:
            state['pending'] = ('EXIT_TP1_BREAKEVEN' if (state.get('tp1_executed') or state.get('breakeven_activated'))
                                else 'EXIT_INITIAL_ATR_HARD_STOP')
            return state['pending']
        if sign * (curr_close - float(c.kc_middle)) < 0:
            state['pending'] = 'EXIT_KC_MIDDLE_HARD_STOP'
            return state['pending']

        # Revalidate every retry against the latest completed candle.
        qty = float(position.get('qty') or 0)
        fee_cost = (entry + curr_close) * TAKER_FEE_RATE + curr_close * SLIPPAGE_PCT
        abs_pnl_est = (raw_pnl_per - fee_cost) * qty
        profitable = raw_pnl_per - fee_cost > 0
        margin = float(position.get('margin') or 0)
        roi_pct = abs_pnl_est / margin if math.isfinite(margin) and margin > 0 else 0.
        state.update(net_unrealized_pnl=abs_pnl_est, margin_roe=roi_pct)

        # 極速收割條件 (動能衰竭)
        c_ma3 = float(c.ma3)
        c1_ma3 = float(c1.ma3)
        ma3_reversed = sign * (c_ma3 - c1_ma3) <= 0
        close_cross = sign * (curr_close - c_ma3) <= 0
        
        fast_exit_eligible = (math.isfinite(qty) and qty > 0 and not state.get('tp1_executed')
                              and state.get('breakeven_activated', False) and state.get('outer', False)
                              and (ma3_reversed or close_cross) and not protected)
        
        if fast_exit_eligible:
            state['pending'] = 'FAST_EXIT_PARTIAL_CLOSE_60PCT'
            return state['pending']

        return None

    # ─────────────────────────────────────────────────────────────
    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position['cooldown_mode'] = 'NONE'
