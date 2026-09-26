"""Single close-only A-E contract shared by scans and the order boundary.

規則精要（多空對稱，以多單為例）：
  A  - 前一根為紅K，當根收出實體長綠（>0.8 ATR）→ 開多
  B  - 前根小陰 <=0.6 前根ATR，當根大陽 >=1.0 當根ATR 完整吞噬，MA3 上升
  C  - 金叉瞬間（前根ma3<=ma15，當根ma3>ma15），斜率向上，收盤在中軌上方 → 開多
  D  - 前根突破上軌，當根確認在上軌外（two-bar breakout），ma3>ma15 → 開多
  E  - 三根收盤軌外，當根實體 >=0.8 ATR，MA3 順向且距中軌 <=2.2 ATR

空單規則完全對稱。
"""
import math

from core.interfaces.entry_interface import IEntryStrategy
from core.services.candle_data import closed_entry_candles

RULE_CODES = frozenset(f"CLOSED_{rule}_{side}" for rule in "ABCDE" for side in ("LONG", "SHORT"))


def at_least(value, threshold):
    """Tolerate arithmetic rounding only at inclusive boundaries."""
    return value >= threshold or math.isclose(value, threshold, rel_tol=1e-12, abs_tol=0.)


def confirmed(frame):
    """Return only fully-closed 1M rows; None if data is invalid."""
    closed = closed_entry_candles(frame)
    if len(closed) < 3 or frame.attrs.get('timeframe_ms', 60000) != 60000:
        return None
    try:
        required = ['timestamp', 'open', 'high', 'low', 'close', 'atr',
                    'ma3', 'ma15', 'kc_upper', 'kc_lower', 'kc_middle']
        rows = closed.iloc[-3:][required].astype(float)
        if not all(math.isfinite(v) and v > 0 for v in rows.to_numpy().flat):
            return None
        if not (rows.timestamp.diff().dropna() == 60000).all():
            return None
        if not ((rows.low <= rows[['open', 'close']].min(axis=1)) &
                (rows.high >= rows[['open', 'close']].max(axis=1)) &
                (rows.kc_lower < rows.kc_middle) &
                (rows.kc_middle < rows.kc_upper)).all():
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return closed


def ma3_entry_problem(closed, side):
    """Every rule requires strictly directional MA3 slope and MA15 alignment."""
    if side not in ('LONG', 'SHORT') or closed is None or len(closed) < 2:
        return 'BLOCKED_INVALID_MA3_DATA'
    try:
        previous, current = closed.iloc[-2], closed.iloc[-1]
        values = [float(previous.ma3), float(current.ma3), float(current.ma15), float(previous.ma15)]
        if not all(math.isfinite(value) and value > 0 for value in values):
            return 'BLOCKED_INVALID_MA3_DATA'
        sign = 1 if side == 'LONG' else -1
        
        # 1. 均線方向防呆：MA3 方向必須嚴格順向
        if sign * (values[1] - values[0]) <= 0:
            return 'BLOCKED_MA3_SLOPE'
            
        # 2. 逆向交叉過濾：如果 MA3 與 MA15 目前是逆向排列 (死叉開多 / 金叉開空)
        # 必須確保兩者開口正在縮小收斂，嚴禁發散擴大時開單
        is_wrong_alignment = sign * (values[1] - values[2]) <= 0
        if is_wrong_alignment:
            curr_gap = abs(values[1] - values[2])
            prev_gap = abs(values[0] - values[3])
            if curr_gap >= prev_gap:
                return 'BLOCKED_MA3_MA15_DIVERGING'
                
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return 'BLOCKED_INVALID_MA3_DATA'
    return None


def had_close(account, symbol):
    return any(t.get('symbol') == symbol and t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')
               for t in getattr(account, 'trades', []))


def evaluate_closed_entry(frame, side, *, after_close=False):
    wait = lambda reason: (False, reason, dict(action='WAIT', side=side, reason=reason))

    if side not in ('LONG', 'SHORT'):
        return wait('INVALID_SIDE')

    closed = confirmed(frame)
    if closed is None:
        return wait('WAIT_VALID_CLOSED_1M_DATA')

    problem = ma3_entry_problem(closed, side)
    if problem:
        return wait(problem)

    c0, c1, c = (closed.iloc[i] for i in (-3, -2, -1))
    sign = 1 if side == 'LONG' else -1

    # ── 基礎數值 ─────────────────────────────────────────────────
    body = sign * (float(c.close) - float(c.open))   # >0 表示方向正確的實體
    atr = float(c1.atr)                              # 前根已收線 ATR 作為尺度基準

    # 當根必須是正確顏色（多=陽線，空=陰線）
    if body <= 0:
        return wait('BLOCKED_OPPOSITE_CLOSED_BODY')

    prior_body  = sign * (float(c1.close) - float(c1.open))
    older_body  = sign * (float(c0.close) - float(c0.open))
    rail        = 'kc_upper' if side == 'LONG' else 'kc_lower'

    rule = None

    # B: exact body engulfing, current ATR for the impulse and previous ATR
    # for the small opposing candle. Evaluate the more specific rule first.
    current_atr = float(c.atr)
    slope_ok = sign * (float(c.ma3) - float(c1.ma3)) > 0
    engulfed = (sign * (float(c.close) - float(c1.open)) > 0 and
                sign * (float(c.open) - float(c1.close)) <= 0)
    if (prior_body < 0 and at_least(0.6 * float(c1.atr), abs(prior_body))
            and at_least(body, current_atr) and engulfed and slope_ok):
        rule = 'B'

    # A: an immediately preceding opposing candle, followed by a long body.
    if rule is None and body > 0.8 * atr and prior_body < 0 and slope_ok:
        rule = 'A'

    # ── Rule C：MA3 金/死叉 MA15（穿越瞬間，斜率＋位置過濾）───────
    # 前根 ma3 在 ma15 同側或相等，當根剛剛穿越；斜率順向；收盤在中軌正確一側
    if rule is None:
        crossed = sign * (c1.ma3 - c1.ma15) <= 0 < sign * (c.ma3 - c.ma15)
        slope_ok = sign * (c.ma3 - c1.ma3) > 0
        side_of_middle = sign * (c.close - c.kc_middle) > 0
        kc_slope_ok = sign * (float(c.kc_middle) - float(c1.kc_middle)) > 0
        if crossed and slope_ok and side_of_middle and kc_slope_ok:
            rule = 'C'

    # ── Rule D：兩根破軌確認突破（前根突破，當根確認在軌外）─────────
    # 前根已收線在軌道外，當根再度收在軌道外，且 ma3 方向正確
    if rule is None:
        prev_outside = sign * (float(c1.close) - float(c1[rail])) > 0
        curr_outside = sign * (float(c.close) - float(c[rail])) > 0
        ma_aligned   = sign * (float(c.ma3) - float(c.ma15)) > 0
        if prev_outside and curr_outside and ma_aligned and body >= 0.8 * current_atr:
            rule = 'D'

    # E: exact three-close outside-rail relay; no historic extreme or wick filter.
    if rule is None:
        all_outside = all(sign * (float(row.close) - float(row[rail])) > 0
                          for row in (c0, c1, c))
        distance = sign * (float(c.close) - float(c.kc_middle))
        if (all_outside and body > 0 and slope_ok
                and at_least(2.2 * current_atr, distance)):
            rule = 'E'

    if rule is None:
        return wait('WAIT_NEW_A_TO_E_TRIGGER')

    code = f'CLOSED_{rule}_{side}'
    return True, code, dict(
        action='ENTER', side=side, reason=code, rule=rule,
        entry_type=rule, entry_atr=atr, is_breakout=rule in ('D', 'E'),
        confirmation_bar_id=float(c.timestamp), close_price=float(c.close)
    )


def check_streamlined_entry_signal(df, side, live_price, position_status, **kwargs):
    if position_status != 'NO_POSITION':
        return False, 'WAIT_EXISTING_POSITION', {'action': 'WAIT'}
    return evaluate_closed_entry(df, side, after_close=kwargs.get('after_close', False))


def check_ma_cross_entry(df):
    for side in ('LONG', 'SHORT'):
        ok, _, decision = evaluate_closed_entry(df, side)
        if ok and decision['rule'] == 'C':
            return decision
    return None


class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if kwargs.get('existing_pos'):
            return False, 'WAIT_EXISTING_POSITION', {'action': 'WAIT'}
        engine = kwargs.get('engine')
        after_close = (had_close(engine.account, kwargs.get('symbol', ''))
                       if engine is not None else kwargs.get('after_close', False))
        return evaluate_closed_entry(frame, side, after_close=after_close)
