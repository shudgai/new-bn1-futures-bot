"""Closed one-minute exits — 方案A階梯鎖利 + 獲利門檻保護 + MA3峰谷V點止盈.

出場三階段（多空對稱）：

  【第一階段】TP1 50% 鎖利
    ROI >= 15% 或 浮盈 >= TP1_USDT_FLOOR(15U) → 市價平 50%，標記 tp1_executed

  【第二階段】保本防線
    TP1 後剩餘 50% 止損強制移至開倉成本（含手續費緩衝）

  【第三階段】剩餘倉位動態出場（分兩種情境）
    情境 A：利潤已達次級門檻（ROI>=5% 或 浮盈>=10U）→ 開放止盈出場
      優先級 1: 脫軌加速區 MA3 峰谷 V 點即刻平倉
      優先級 2: 大吞噬長K反轉
      優先級 3: 脫軌區 MA3 穿越
      優先級 4: 收盤跌破 KC 中軌
      優先級 5: 動態 ATR 追蹤防線
    情境 B：利潤未達次級門檻（微利/虧損）→ 嚴格禁止 MA3 峰谷割肉
      只允許：收盤跌破 KC 中軌 | 1.5 ATR 初始止損

大實體長K保護：
    順向實體 > 0.8 ATR → 任何平倉訊號均 pending，防賣飛
"""
import math
from core.interfaces.exit_interface import IExitStrategy
from core.services.strategies.unified_entry_strategy import confirmed
from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT

POLICY = 'closed_1m_v3_profit_gate'
DUAL_TRACK_STATE_KEYS = [
    'closed_exit_state', 'sl', 'tp',
    'entry_atr', 'atr_sl', 'atr_tp', 'atr_protection_version'
]

# ─── 方案A 參數 ────────────────────────────────────────────────────
TP1_ROI_PCT       = 0.15    # 第一階段：ROI 達 15% 觸發 50% 鎖利
TP1_USDT_FLOOR    = 15.0    # 第一階段：小本金絕對浮盈兜底（USDT）
TP1_FRACTION      = 0.50    # 平倉比例
BREAKEVEN_BUFFER  = 0.0002  # 保本緩衝（約雙邊手續費）

# ─── 次級獲利門檻（允許 MA3 峰谷止盈的最低條件）────────────────────
MIN_PROFIT_ROI    = 0.05    # ROI >= 5% 才允許 MA3 峰谷止盈
MIN_PROFIT_USDT   = 10.0    # 絕對浮盈 >= 10U 才允許 MA3 峰谷止盈

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
        atr   = float(c1.atr)   # 前根已收線 ATR 作為尺度基準

        # ── 初始化或重建狀態 ───────────────────────────────────
        identity = [side, entry, opened]
        state = position.get('closed_exit_state')
        if not isinstance(state, dict) or state.get('identity') != identity or state.get('policy') != POLICY:
            state = dict(
                policy=POLICY, identity=identity,
                peak=entry, last_bar=-1,
                stop=entry - sign * SL_INIT_MULT * atr,
                outer=False, pending=None,
                tp1_executed=bool(position.get('is_half_closed')),
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
        if bar == state['last_bar']:
            return None if protected else state.get('pending')
        state['last_bar'] = bar

        # ── 更新持倉期間的極值（峰/谷）────────────────────────
        peak = max(sign * state['peak'], sign * curr_close) * sign
        state['peak'] = peak

        # ── 軌道外標記（曾突破外軌才啟用脫軌止盈）─────────────
        rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
        outside          = sign * (curr_close           - float(c[rail]))  >= 0
        previous_outside = sign * (float(c1.close)      - float(c1[rail])) >= 0
        state['outer'] = state['outer'] or outside or previous_outside

        # ── 動態 ATR 追蹤防線 ─────────────────────────────────
        mult      = ATR_TRAIL_OUTER if (outside or previous_outside) else ATR_TRAIL_NORMAL
        candidate = peak - sign * mult * atr
        # TP1 後把候選防線底線拉至保本
        if state.get('tp1_executed'):
            breakeven = entry + sign * entry * BREAKEVEN_BUFFER
            candidate = max(sign * candidate, sign * breakeven) * sign
        stop = max(sign * state['stop'], sign * candidate) * sign
        state['stop'] = stop
        position.update(sl=stop, atr_sl=stop, tp=0., atr_tp=0.)

        # ── 大實體保護期不發出訊號 ─────────────────────────────
        if protected:
            state['pending'] = None
            return None

        # ── pending 重試（前根失敗重試）────────────────────────
        if state.get('pending'):
            return state['pending']

        # ══════════════════════════════════════════════════════
        # 計算浮盈指標
        # ══════════════════════════════════════════════════════
        qty         = float(position.get('qty') or 0)
        fee_cost    = (entry + curr_close) * TAKER_FEE_RATE + curr_close * SLIPPAGE_PCT
        raw_pnl_per = sign * (curr_close - entry)            # 每單位浮盈
        roi_pct     = raw_pnl_per / entry if entry > 0 else 0.0
        abs_pnl_est = raw_pnl_per * qty - fee_cost * qty     # 估算絕對浮盈（USDT）
        profitable  = raw_pnl_per - fee_cost > 0             # 扣費後仍盈利

        reason = None

        # ══════════════════════════════════════════════════════
        # 【第一階段】TP1：ROI >= 15% 或浮盈 >= 15U → 平 50%
        # ══════════════════════════════════════════════════════
        if not state.get('tp1_executed'):
            if roi_pct >= TP1_ROI_PCT or abs_pnl_est >= TP1_USDT_FLOOR:
                state['pending'] = 'TP1_PARTIAL_CLOSE_50PCT'
                return 'TP1_PARTIAL_CLOSE_50PCT'

        # ══════════════════════════════════════════════════════
        # 判斷是否達到「次級獲利門檻」
        # 未達門檻時嚴格禁止 MA3 峰谷割肉
        # ══════════════════════════════════════════════════════
        profit_gate_open = (roi_pct >= MIN_PROFIT_ROI or abs_pnl_est >= MIN_PROFIT_USDT)

        # ══════════════════════════════════════════════════════
        # 【第三階段 - 情境A】有利潤保護時的完整止盈邏輯
        # ══════════════════════════════════════════════════════
        if profit_gate_open:
            # 優先級 1：脫軌加速區 MA3 峰谷 V 點即刻平倉
            if profitable and state['outer']:
                ma3_reversed = sign * (float(c.ma3)  - float(c1.ma3)) < 0
                close_cross  = sign * (curr_close     - float(c.ma3)) < 0
                if ma3_reversed and close_cross:
                    reason = 'EXIT_OUTER_MA3_CURVE_CLOSED'

            # 優先級 2：大吞噬長K反轉
            if reason is None and profitable and state['outer']:
                body_neg  = body_signed < 0
                prev_pos  = sign * (float(c1.close) - float(c1.open)) > 0
                engulfing = sign * (curr_close - float(c1.open)) < 0
                if body_neg and prev_pos and engulfing:
                    reason = 'EXIT_OUTER_ENGULFING_CLOSED'

            # 優先級 3：脫軌區 MA3 穿越兜底
            if reason is None:
                close_cross_ma3 = sign * (curr_close - float(c.ma3)) < 0
                if body_signed < 0 and (outside or previous_outside) and close_cross_ma3:
                    reason = 'EXIT_OUTER_MA3_CLOSED'

        # ══════════════════════════════════════════════════════
        # 【第三階段 - 情境B】無論有無利潤，均允許的客觀防線
        # ══════════════════════════════════════════════════════
        # 防線1：收盤跌穿 KC 中軌（趨勢實質逆轉）
        if reason is None and body_signed < 0 and sign * (curr_close - float(c.kc_middle)) < 0:
            reason = 'EXIT_KC_MIDDLE_CLOSED'

        # 防線2：動態 ATR 追蹤止損（最後兜底）
        if reason is None and sign * (curr_close - stop) <= 0:
            reason = 'EXIT_ATR_TRAIL_CLOSED'

        state['pending'] = reason
        return reason

    # ─────────────────────────────────────────────────────────────
    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position['cooldown_mode'] = 'NONE'
