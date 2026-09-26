"""Single close-only A-E contract shared by scans and the order boundary.

規則精要（多空對稱，以多單為例）：
  A  - 前一或前兩根為紅K，當根收出實體長綠（>0.8 ATR）→ 開多
  B  - 前根小陰，當根大陽吞噬（>1.2 ATR）→ 開多
  C  - 金叉瞬間（前根ma3<=ma15，當根ma3>ma15），斜率向上，收盤在中軌上方 → 開多
  D  - 前根突破上軌，當根確認在上軌外（two-bar breakout），ma3>ma15 → 開多
  E  - 脫軌區已在軌外連續運行，當根長陽實體>0.8 ATR 且創新高 → 追多（after_close only）

空單規則完全對稱。
"""
import math

from core.interfaces.entry_interface import IEntryStrategy
from core.services.candle_data import closed_entry_candles

RULE_CODES = frozenset(f"CLOSED_{rule}_{side}" for rule in "ABCDE" for side in ("LONG", "SHORT"))
SMALL_BODY_ATR = 0.5      # Rule B: prior bar body must be ≤ this × ATR
RELAY_LOOKBACK = 20       # Rule E: look-back window for new extreme
MAX_DIRECTIONAL_WICK_BODY = 0.5  # Rule E: wick/body cap
MAX_RELAY_MIDDLE_ATR = 2.0  # Rule E: 距 KC 中軌乖離上限，超過禁止追入（防頂部接刀）


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

    c0, c1, c = (closed.iloc[i] for i in (-3, -2, -1))
    sign = 1 if side == 'LONG' else -1

    # ── 基礎數值 ─────────────────────────────────────────────────
    body = sign * (float(c.close) - float(c.open))   # >0 表示方向正確的實體
    atr = float(c1.atr)                              # 前根已收線 ATR 作為尺度基準

    # 當根必須是正確顏色（多=陽線，空=陰線）
    if body < 0:
        return wait('BLOCKED_OPPOSITE_CLOSED_BODY')

    prior_body  = sign * (float(c1.close) - float(c1.open))
    older_body  = sign * (float(c0.close) - float(c0.open))
    rail        = 'kc_upper' if side == 'LONG' else 'kc_lower'

    rule = None

    # ── Rule A：紅轉綠長陽 / 綠轉紅長陰（反手型，最高優先）────────
    # 前一或前兩根為逆方向K棒，當根收出方向正確的長實體 > 0.8 ATR
    if rule is None and body > 0.8 * atr and (prior_body < 0 or older_body < 0):
        rule = 'A'

    # ── Rule B：小陰洗盤轉大陽吞噬 / 小陽轉大陰吞噬 ─────────────
    # 前根為小反向線（<=SMALL_BODY_ATR），當根大陽線(>1.2 ATR)完全吞噬前根
    if rule is None:
        if body > 1.2 * atr and abs(prior_body) <= SMALL_BODY_ATR * atr:
            engulfed = (min(c.open, c.close) <= min(c1.open, c1.close) and
                        max(c.open, c.close) >= max(c1.open, c1.close))
            if prior_body < 0 and engulfed:
                rule = 'B'
            elif prior_body > 0 and sign * (c.close - c1.close) > 0:
                # 延伸型吞噬（前根同向但當根大幅超越）
                rule = 'B'

    # ── Rule C：MA3 金/死叉 MA15（穿越瞬間，斜率＋位置過濾）───────
    # 前根 ma3 在 ma15 同側或相等，當根剛剛穿越；斜率順向；收盤在中軌正確一側
    if rule is None:
        crossed = sign * (c1.ma3 - c1.ma15) <= 0 < sign * (c.ma3 - c.ma15)
        slope_ok = sign * (c.ma3 - c1.ma3) > 0
        side_of_middle = sign * (c.close - c.kc_middle) > 0
        if crossed and slope_ok and side_of_middle:
            rule = 'C'

    # ── Rule D：兩根破軌確認突破（前根突破，當根確認在軌外）─────────
    # 前根已收線在軌道外，當根再度收在軌道外，且 ma3 方向正確
    if rule is None:
        prev_outside = sign * (float(c1.close) - float(c1[rail])) > 0
        curr_outside = sign * (float(c.close) - float(c[rail])) > 0
        ma_aligned   = sign * (float(c.ma3) - float(c.ma15)) > 0
        if prev_outside and curr_outside and ma_aligned:
            rule = 'D'

    # ── Rule E：脫軌區中繼長線追擊（after_close only，需有平倉紀錄）──
    # 持續在軌道外連續運行，當根再度拉出長實體且創新極值
    # ⚠️ 乖離保護：若收盤距 KC 中軌超過 MAX_RELAY_MIDDLE_ATR 倍 ATR，禁止高位接刀追多！
    if rule is None and after_close and len(closed) >= RELAY_LOOKBACK + 1 and body > 0.8 * atr:
        previous = closed.iloc[-RELAY_LOOKBACK - 1:-1]
        extreme  = previous.high.max() if side == 'LONG' else previous.low.min()
        wick     = float(c.high) - float(c.close) if side == 'LONG' else float(c.close) - float(c.low)
        all_outside = all(sign * (float(row.close) - float(row[rail])) > 0 for row in (c0, c1, c))
        new_extreme  = sign * (float(c.close) - float(extreme)) > 0
        wick_ok      = wick <= MAX_DIRECTIONAL_WICK_BODY * body
        # 乖離限制：收盤距 KC 中軌的距離不超過 MAX_RELAY_MIDDLE_ATR × ATR
        dist_from_middle = sign * (float(c.close) - float(c.kc_middle))
        overextended = dist_from_middle > MAX_RELAY_MIDDLE_ATR * atr
        if all_outside and new_extreme and wick_ok and not overextended:
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
