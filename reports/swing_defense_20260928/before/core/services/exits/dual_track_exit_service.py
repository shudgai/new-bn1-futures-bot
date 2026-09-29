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
    'closed_exit_state', 'sl', 'tp', 'ma3_trend_hold',
    'entry_atr', 'atr_sl', 'atr_tp', 'atr_protection_version'
]

# ─── 高波動猛幣：動態保本與極速收割參數 ─────────────────────────────
FAST_EXIT_ATR_MULT = 1.5    # 浮盈達 1.5 ATR 啟動保本並允許收割
BREAKEVEN_BUFFER   = 0.0002 # 保本緩衝（約雙邊手續費）

# ─── ATR 防線乘數 ──────────────────────────────────────────────────
SL_INIT_MULT       = 1.5    # 初始止損乘數


def strong_trend_observed(closed, side):
    """Find an aligned pair in the six completed bars before the pullback.

    Six bars and half an ATR inside the outer rail are implementation
    defaults. Looking back also protects existing positions after restart
    when one or two small pullback bars have already occurred.
    """
    if side not in ('LONG', 'SHORT') or len(closed) < 3:
        return False
    sign = 1 if side == 'LONG' else -1
    rows = closed.iloc[-7:-1]
    keys = ('ma15', 'kc_upper', 'kc_middle', 'kc_lower')
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    for index in range(1, len(rows)):
        a, b = rows.iloc[index-1], rows.iloc[index]
        values = [float(row[k]) for row in (a, b)
                  for k in (*keys, 'close', 'atr')]
        if not all(math.isfinite(v) and v > 0 for v in values):
            continue
        if (all(sign * (float(b[k]) - float(a[k])) > 0 for k in keys)
                and all(sign * (float(row.close) - float(row[rail])) >= -.5 * float(row.atr)
                        and sign * (float(row.close) - float(row.ma15)) > 0
                        for row in (a, b))):
            return True
    return False


def evaluate_trend_exit_and_take_profit(position: dict, candles: list, indicators: dict) -> dict:
    """
    純趨勢動態鎖利與轉折出場審查
    回傳: {"should_exit": bool, "action": "FULL_CLOSE" | "HOLD", "reason": str}
    """
    side = position['side']          # 'LONG' 或 'SHORT'
    entry_price = float(position['entry_price'])
    curr_close = float(candles[-1]['close'])
    atr = float(indicators['atr'][-1])
    
    ma3_curr = float(indicators['ma3'][-1])
    ma3_prev = float(indicators['ma3'][-2])
    
    buffer = 0.2 * atr
    sign = 1 if side == 'LONG' else -1
    trend_break = False
    for key in ('ma15', 'kc_middle'):
        values = indicators.get(key, [])
        if values:
            level = float(values[-1])
            if math.isfinite(level) and level > 0 and sign * (curr_close - level) < 0:
                trend_break = True
    allow_ma3_exit = not position.get('ma3_trend_hold', False) or trend_break
    # -------------------------------------------------------------------------
    # 1. 多單鎖利與出場 (LONG)
    # -------------------------------------------------------------------------
    if side == "LONG":
        # A. 頂部動能轉向全平：MA3 拐頭走平/向下 且 收盤跌破 MA3 (包含 0.2 ATR 防洗盤緩衝)
        if allow_ma3_exit and ma3_curr <= ma3_prev and curr_close < (ma3_curr - buffer):
            print(f"[MA3_EXIT_LONG] curr_close={curr_close:.6f}, ma3_curr={ma3_curr:.6f}, buffer={buffer:.6f}, trigger_reason=MA3_PEAK_REVERSAL_FULL_CLOSE")
            return {
                "should_exit": True,
                "action": "FULL_CLOSE",
                "reason": "MA3_PEAK_REVERSAL_FULL_CLOSE"
            }
            
        # B. 保本防線推進 (浮盈達 1.2 ATR 時，鎖死成本線上方 0.1 ATR)
        unrealized_profit = curr_close - entry_price
        if unrealized_profit >= 1.2 * atr:
            position['stop_loss'] = max(float(position.get('stop_loss', 0)), entry_price + (0.1 * atr))

    # -------------------------------------------------------------------------
    # 2. 空單鎖利與出場 (SHORT)
    # -------------------------------------------------------------------------
    elif side == "SHORT":
        # A. 底部動能轉向全平：MA3 拐頭走平/向上 且 收盤突破 MA3 (包含 0.2 ATR 防抽容忍空間)
        if allow_ma3_exit and ma3_curr >= ma3_prev and curr_close > (ma3_curr + buffer):
            print(f"[MA3_EXIT_SHORT] curr_close={curr_close:.6f}, ma3_curr={ma3_curr:.6f}, buffer={buffer:.6f}, trigger_reason=MA3_BOTTOM_REVERSAL_FULL_CLOSE")
            return {
                "should_exit": True,
                "action": "FULL_CLOSE",
                "reason": "MA3_BOTTOM_REVERSAL_FULL_CLOSE"
            }
            
        # B. 保本防線推進 (浮盈達 1.2 ATR 時，鎖死成本線下方 0.1 ATR)
        unrealized_profit = entry_price - curr_close
        if unrealized_profit >= 1.2 * atr:
            position['stop_loss'] = min(float(position.get('stop_loss', float('inf'))), entry_price - (0.1 * atr))

    return {"should_exit": False, "action": "HOLD", "reason": "TREND_RUNNING"}


class DualTrackExitStrategy(IExitStrategy):

    def initialize_position(self, position, entry_price, atr):
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(
            entry_atr=float(atr),
            sl=entry_price - sign * SL_INIT_MULT * atr,
            stop_loss=entry_price - sign * SL_INIT_MULT * atr,
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

        # 需要最近兩根已收線
        if len(closed) < 2:
            return None
        c1, c = closed.iloc[-2], closed.iloc[-1]
        bar = float(c.timestamp)

        # 不用早於開倉時間的 K 棒管理倉位
        if bar + 60000 <= opened or bar <= float(position.get('channel_confirmation_bar_id') or -1):
            return None
            
        candles = [
            {'close': float(c1.close)},
            {'close': float(c.close)}
        ]
        
        indicators = {
            'atr': [float(c1.atr), float(c.atr)],
            'ma3': [float(c1.ma3), float(c.ma3)],
            'ma15': [float(c1.ma15), float(c.ma15)],
            'kc_middle': [float(c1.kc_middle), float(c.kc_middle)]
        }
        
        # 確保 stop_loss 在 position 裡， fallback 給 sl
        if 'stop_loss' not in position:
            position['stop_loss'] = position.get('sl', 0)
            if position['stop_loss'] == 0:
                sign = 1 if position['side'] == 'LONG' else -1
                atr = float(position.get('entry_atr') or c1.atr)
                position['stop_loss'] = entry - sign * SL_INIT_MULT * atr

        # Once observed during this position, do not lose protection just
        # because the next pullback weakens the current rail slope.
        if strong_trend_observed(closed, position['side']):
            position['ma3_trend_hold'] = True
        result = evaluate_trend_exit_and_take_profit(position, candles, indicators)
        
        # update sl mappings
        position['sl'] = position['stop_loss']
        position['atr_sl'] = position['stop_loss']
        position['tp'] = 0.0
        position['atr_tp'] = 0.0
        
        if result['should_exit'] and result['action'] == "FULL_CLOSE":
            return result['reason']
            
        # check hard stop loss
        curr_close = float(c.close)
        sign = 1 if position['side'] == 'LONG' else -1
        stop = float(position['stop_loss'])
        if sign * (curr_close - stop) <= 0:
            # 檢查是初始止損還是已保本
            is_breakeven = False
            if position['side'] == 'LONG' and stop > entry:
                is_breakeven = True
            elif position['side'] == 'SHORT' and stop < entry:
                is_breakeven = True
            return 'EXIT_BREAKEVEN' if is_breakeven else 'EXIT_INITIAL_ATR_HARD_STOP'
            
        return None

    # ─────────────────────────────────────────────────────────────
    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position['cooldown_mode'] = 'NONE'
