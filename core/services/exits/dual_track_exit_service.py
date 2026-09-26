"""Closed one-minute exits — 方案A階梯鎖利 + MA3峰谷V點反轉 + 動態ATR防線.

出場三階段（多空對稱）：
  第一階段  TP1  : ROI >= 15% 或浮盈 >= 15 USDT → 市價平 50%，標記 TP1_EXECUTED
  第二階段  保本  : TP1 後剩餘 50% 止損移至開倉成本（保本線）
  第三階段  剩餘  : MA3峰谷V點即刻平（脫軌優先）→ 動態ATR防線兜底

大實體長K保護（防賣飛）：
  順向實體 > 0.8 ATR → 任何平倉指令均不執行（pending 重試）
"""
import math
from core.interfaces.exit_interface import IExitStrategy
from core.services.strategies.unified_entry_strategy import confirmed
from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT

POLICY = 'closed_1m_v2_ladder'
DUAL_TRACK_STATE_KEYS = [
    'closed_exit_state', 'sl', 'tp',
    'entry_atr', 'atr_sl', 'atr_tp', 'atr_protection_version'
]

# 方案A參數
TP1_ROI_PCT      = 0.15    # ROI 達 15% 觸發 TP1
TP1_USDT_FLOOR   = 15.0    # 小本金絕對金額兜底
TP1_FRACTION     = 0.50    # 平倉 50%
BREAKEVEN_BUFFER = 0.0002  # 保本緩衝（手續費方向）

# ATR 防線乘數
ATR_TRAIL_OUTER  = 0.8     # 脫軌時收窄
ATR_TRAIL_NORMAL = 1.5     # 軌道內
SL_INIT_MULT     = 1.5     # 初始止損


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
            return self._evaluate(position, frame, closed)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    # ─────────────────────────────────────────────────────────────
    def _evaluate(self, position, frame, closed):
        entry  = float(position['entry_price'])
        opened = float(position.get('open_timestamp') or 0) * 1000
        if not math.isfinite(entry) or entry <= 0 or not math.isfinite(opened):
            return None

        c0, c1, c = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
        bar  = float(c.timestamp)

        # 不用開倉當根或早於開倉時間的 K 棒來管理倉位
        if bar + 60000 <= opened or bar <= float(position.get('channel_confirmation_bar_id') or -1):
            return None

        side  = position['side']
        sign  = 1 if side == 'LONG' else -1
        atr   = float(c1.atr)   # 前根已收線 ATR

        # ── 初始化狀態 ─────────────────────────────────────────
        identity = [side, entry, opened]
        state = position.get('closed_exit_state')
        if not isinstance(state, dict) or state.get('identity') != identity or state.get('policy') != POLICY:
            state = dict(
                policy=POLICY, identity=identity,
                peak=entry, last_bar=-1,
                stop=entry - sign * SL_INIT_MULT * atr,
                outer=False, pending=None,
                tp1_executed=False,
                is_half=bool(position.get('is_half_closed')),
            )
            position['closed_exit_state'] = state

        # 同步 is_half（partial_close 後 position 會被標記）
        if position.get('is_half_closed') and not state.get('tp1_executed'):
            state['tp1_executed'] = True
            state['is_half'] = True

        body_signed = sign * (float(c.close) - float(c.open))   # >0 = 方向一致的長K

        # ── 大實體長K保護（防賣飛）──────────────────────────────
        protected = body_signed > 0.8 * atr

        # ── 去重：同根只處理一次 ──────────────────────────────
        if bar < state['last_bar']:
            return None
        if bar == state['last_bar']:
            return None if protected else state.get('pending')
        state['last_bar'] = bar

        # ── 更新峰值 ─────────────────────────────────────────
        peak = max(sign * state['peak'], sign * float(c.close)) * sign
        state['peak'] = peak

        # ── 軌道外標記 ───────────────────────────────────────
        rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
        outside          = sign * (float(c.close)  - float(c[rail]))  >= 0
        previous_outside = sign * (float(c1.close) - float(c1[rail])) >= 0
        state['outer'] = state['outer'] or outside or previous_outside

        # ── 動態 ATR 防線（trailing stop）─────────────────────
        mult     = ATR_TRAIL_OUTER if (outside or previous_outside) else ATR_TRAIL_NORMAL
        candidate = peak - sign * mult * atr
        if state.get('tp1_executed'):
            # TP1 後把防線拉回保本
            breakeven = entry + sign * entry * BREAKEVEN_BUFFER
            candidate = max(sign * candidate, sign * breakeven) * sign
        stop = max(sign * state['stop'], sign * candidate) * sign
        state['stop'] = stop
        position.update(sl=stop, atr_sl=stop, tp=0., atr_tp=0.)

        # 大實體保護期不發出平倉訊號
        if protected:
            state['pending'] = None
            return None

        # ── pending 重試 ──────────────────────────────────────
        if state.get('pending'):
            return state['pending']

        # ── 盈虧計算 ──────────────────────────────────────────
        cost   = (entry + float(c.close)) * TAKER_FEE_RATE + float(c.close) * SLIPPAGE_PCT
        unrealized_pnl_raw = sign * (float(c.close) - entry)
        # 以「整倉」ROI 衡量（即使已半平，保本條件以原始 entry 為基準）
        roi_pct    = unrealized_pnl_raw / entry if entry > 0 else 0.0
        profitable = unrealized_pnl_raw - cost > 0

        reason = None

        # ════════════════════════════════════════════════════
        # 【第一階段】方案A TP1：ROI >= 15% 或浮盈 >= 15 USDT
        # ════════════════════════════════════════════════════
        if not state.get('tp1_executed'):
            # 以 position 的 qty 估算絕對浮盈
            qty         = float(position.get('qty') or 0)
            abs_pnl_est = unrealized_pnl_raw * qty - cost * qty
            if roi_pct >= TP1_ROI_PCT or abs_pnl_est >= TP1_USDT_FLOOR:
                state['pending'] = 'TP1_PARTIAL_CLOSE_50PCT'
                return 'TP1_PARTIAL_CLOSE_50PCT'

        # ════════════════════════════════════════════════════
        # 【第三階段】MA3 峰谷 V 點即刻平倉（脫軌加速段優先）
        # ════════════════════════════════════════════════════
        if profitable and state['outer']:
            ma3_reversed  = sign * (float(c.ma3) - float(c1.ma3)) < 0  # MA3 開始反轉
            close_cross   = sign * (float(c.close) - float(c.ma3)) < 0  # 收盤已穿破MA3
            if ma3_reversed and close_cross:
                reason = 'EXIT_OUTER_MA3_CURVE_CLOSED'

        # ── 吞噬型大反轉 ─────────────────────────────────────
        if reason is None and profitable and state['outer']:
            body_neg     = body_signed < 0
            prev_pos     = sign * (float(c1.close) - float(c1.open)) > 0
            engulfing    = sign * (float(c.close) - float(c1.open)) < 0
            if body_neg and prev_pos and engulfing:
                reason = 'EXIT_OUTER_ENGULFING_CLOSED'

        # ── 脫軌 MA3 穿越（基礎兜底）─────────────────────────
        if reason is None:
            close_cross_ma3 = sign * (float(c.close) - float(c.ma3)) < 0
            if body_signed < 0 and (outside or previous_outside) and close_cross_ma3:
                reason = 'EXIT_OUTER_MA3_CLOSED'

        # ── 跌回中軌（趨勢反轉基礎防線）─────────────────────
        if reason is None and body_signed < 0 and sign * (float(c.close) - float(c.kc_middle)) < 0:
            reason = 'EXIT_KC_MIDDLE_CLOSED'

        # ── 動態 ATR 追蹤防線 ────────────────────────────────
        if reason is None and sign * (float(c.close) - stop) <= 0:
            reason = 'EXIT_ATR_TRAIL_CLOSED'

        state['pending'] = reason
        return reason

    # ─────────────────────────────────────────────────────────────
    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position['cooldown_mode'] = 'NONE'
