import math


class PositionDefenseState:
    def __init__(self, entry_price: float, qty: float, side: str, snapshot_atr: float, mode: str = "TREND"):
        self.entry_price = entry_price
        self.qty = qty
        self.side = side
        self.snapshot_atr = snapshot_atr
        self.mode = mode

        # 0: 未啟動, 1: 保本, 2: 階梯鎖利, 3: 極限防禦
        self.stage = 0
        self.current_sl_price = 0.0
        self.highest_pnl_atr = 0.0
        self.highest_pnl_pct = 0.0


class TieredExitManager:
    def __init__(self,
                 stage1_trigger_atr=1.0,
                 stage2_trigger_atr=1.5,
                 stage2_buffer_atr=0.7,
                 stage3_trigger_atr=2.5,
                 stage3_giveback_ratio=0.15,
                 fee_buffer_pct=0.001):

        self.stage1_trigger_atr = stage1_trigger_atr
        self.stage2_trigger_atr = stage2_trigger_atr
        self.stage2_buffer_atr = stage2_buffer_atr
        self.stage3_trigger_atr = stage3_trigger_atr
        self.stage3_giveback_ratio = stage3_giveback_ratio
        self.fee_buffer_pct = fee_buffer_pct

        self.modes = {
            "EXPLOSIVE": {"stage2_trigger_atr": 1.5, "stage2_buffer_atr": 0.4, "stage3_giveback_ratio": 0.15},
            "TREND":     {"stage2_trigger_atr": 1.5, "stage2_buffer_atr": 1.0, "stage3_giveback_ratio": 0.15}
        }

    def _ratchet_sl(self, state: PositionDefenseState, new_sl: float) -> bool:
        """棘輪機制：多單 SL 只升不降，空單 SL 只降不升。"""
        if state.current_sl_price <= 0:
            state.current_sl_price = new_sl
            return True
        if state.side == "LONG":
            if new_sl > state.current_sl_price + state.entry_price * 1e-6:
                state.current_sl_price = new_sl
                return True
        elif state.side == "SHORT":
            if new_sl < state.current_sl_price - state.entry_price * 1e-6:
                state.current_sl_price = new_sl
                return True
        return False

    def update_tick(self, state: PositionDefenseState, live_price: float,
                    kc_upper: float = 0., kc_middle: float = 0.,
                    ma5: float = 0.) -> dict:
        """
        三階梯棘輪防線 (3-Tier Ratchet Trailing Stop)
        Stage 1 保本: 浮盈 >= 1.0 ATR 或 >= 3%  → 止損移至開倉價+手續費
        Stage 2 鎖利: 浮盈 >= 1.5 ATR 或 >= 8%  → 止損移至 KC 上軌 or 開倉價+0.8 ATR
        Stage 3 追蹤: 浮盈 >= 2.5 ATR 或 >= 15% → 止損錨定（峰值-0.8 ATR）與 MA5 較有利者
        空單完全對稱。防線棘輪：只進不退。
        """
        if not math.isfinite(live_price) or live_price <= 0:
            return {"action": "NONE"}

        entry = state.entry_price
        atr = state.snapshot_atr
        sign = 1 if state.side == "LONG" else -1

        if atr <= 0 or entry <= 0:
            return {"action": "NONE"}

        raw_gain = sign * (live_price - entry)
        gain_atr = raw_gain / atr
        gain_pct = raw_gain / entry

        if gain_atr > state.highest_pnl_atr:
            state.highest_pnl_atr = gain_atr
        if gain_pct > state.highest_pnl_pct:
            state.highest_pnl_pct = gain_pct

        peak_price = entry + sign * state.highest_pnl_atr * atr
        target_sl = 0.0
        new_stage = state.stage

        if state.highest_pnl_atr >= self.stage3_trigger_atr or state.highest_pnl_pct >= 0.15:
            stage3_sl = peak_price - sign * 0.8 * atr
            if ma5 > 0:
                stage3_sl = max(stage3_sl, ma5) if sign == 1 else min(stage3_sl, ma5)
            target_sl = stage3_sl
            new_stage = 3

        elif state.highest_pnl_atr >= self.stage2_trigger_atr or state.highest_pnl_pct >= 0.08:
            sl_08atr = entry + sign * 0.8 * atr
            if sign == 1 and kc_upper > 0 and kc_upper > entry:
                stage2_sl = max(sl_08atr, kc_upper)
            elif sign == -1 and kc_middle > 0 and kc_middle < entry:
                stage2_sl = min(sl_08atr, kc_middle)
            else:
                stage2_sl = sl_08atr
            target_sl = stage2_sl
            new_stage = max(new_stage, 2)

        elif state.highest_pnl_atr >= self.stage1_trigger_atr or state.highest_pnl_pct >= 0.03:
            fee_buf = entry * self.fee_buffer_pct
            target_sl = entry + sign * fee_buf
            new_stage = max(new_stage, 1)

        state.stage = new_stage

        # 棘輪更新
        improved = False
        if target_sl > 0:
            improved = self._ratchet_sl(state, target_sl)

        current_sl = state.current_sl_price
        if current_sl > 0:
            breached = (sign == 1 and live_price < current_sl) or \
                       (sign == -1 and live_price > current_sl)
            if breached:
                return {
                    "action": "MARKET_EXIT",
                    "sl_price": current_sl,
                    "reason": f"RATCHET_SL_BREACH_STAGE{state.stage}",
                }

        if improved:
            return {
                "action": "UPDATE_SL",
                "sl_price": state.current_sl_price,
                "reason": f"RATCHET_TRAIL_STAGE{state.stage}",
            }

        return {"action": "NONE"}
