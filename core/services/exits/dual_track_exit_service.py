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

# ─── 方案A 參數 ────────────────────────────────────────────────────
TP1_ROI_PCT       = 0.15    # Net margin ROE >=15% triggers TP1
TP1_USDT_FLOOR    = 5.0    # 第一階段：小本金絕對浮盈兜底（USDT）
TP1_FRACTION      = 0.50    # 平倉比例
BREAKEVEN_BUFFER  = 0.0002  # 保本緩衝（約雙邊手續費）

# ─── 次級獲利門檻（允許 MA3 峰谷止盈的最低條件）────────────────────
MIN_PROFIT_ROI    = 0.05    # ROI >= 5% 才允許 MA3 峰谷止盈
MIN_PROFIT_USDT   = 2.0    # Net unrealized PnL >=2U permits MA3 exits

# ─── ATR 防線乘數 ──────────────────────────────────────────────────
ATR_TRAIL_OUTER   = 0.8     # 脫軌時收窄
ATR_TRAIL_NORMAL  = 1.5     # 軌道內
SL_INIT_MULT      = 1.5     # 初始止損乘數


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

        # Independent hard exits use the original entry ATR, not the old
        # 0.8 ATR profit trail. TP1 protects the remainder at entry cost.
        stop = entry if state.get('tp1_executed') else entry - sign * SL_INIT_MULT * atr
        state['stop'] = stop
        position.update(sl=stop, atr_sl=stop, tp=0., atr_tp=0.)
        hard_reasons = {'EXIT_INITIAL_ATR_HARD_STOP', 'EXIT_KC_MIDDLE_HARD_STOP',
                        'EXIT_TP1_BREAKEVEN'}
        if state.get('pending') in hard_reasons:
            return state['pending']
        if sign * (curr_close - stop) <= 0:
            state['pending'] = ('EXIT_TP1_BREAKEVEN' if state.get('tp1_executed')
                                else 'EXIT_INITIAL_ATR_HARD_STOP')
            return state['pending']
        if sign * (curr_close - float(c.kc_middle)) < 0:
            state['pending'] = 'EXIT_KC_MIDDLE_HARD_STOP'
            return state['pending']

        # Revalidate every retry against the latest completed candle. Old
        # engulfing/middle/ATR/TP1 pending reasons cannot bypass the MA3 lock.
        qty = float(position.get('qty') or 0)
        fee_cost = (entry + curr_close) * TAKER_FEE_RATE + curr_close * SLIPPAGE_PCT
        raw_pnl_per = sign * (curr_close - entry)
        abs_pnl_est = (raw_pnl_per - fee_cost) * qty
        profitable = raw_pnl_per - fee_cost > 0
        margin = float(position.get('margin') or 0)
        # Actual remaining margin is authoritative, including after a half-close.
        roi_pct = abs_pnl_est / margin if math.isfinite(margin) and margin > 0 else 0.
        state.update(net_unrealized_pnl=abs_pnl_est, margin_roe=roi_pct)
        if (math.isfinite(qty) and qty > 0 and not state.get('tp1_executed')
                and (roi_pct >= TP1_ROI_PCT or abs_pnl_est >= TP1_USDT_FLOOR)):
            state['pending'] = 'TP1_PARTIAL_CLOSE_50PCT'
            return state['pending']

        is_outside = sign * (curr_close - float(c[rail])) > 0
        profit_gate_open = roi_pct >= MIN_PROFIT_ROI or abs_pnl_est >= MIN_PROFIT_USDT

        ma3_reversed = sign * (float(c.ma3) - float(c1.ma3)) < 0
        close_cross = sign * (curr_close - float(c.ma3)) < 0
        eligible = (math.isfinite(qty) and qty > 0 and profitable
                    and is_outside and profit_gate_open
                    and ma3_reversed and close_cross and not protected)
        reason = 'EXIT_OUTER_MA3_CURVE_CLOSED' if eligible else None

        state['pending'] = reason
        return reason

    # ─────────────────────────────────────────────────────────────
    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position['cooldown_mode'] = 'NONE'
