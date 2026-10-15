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
from core.services.strategies.outer_strategy import ck_direction
from core.services.three_bar_rail_gate import three_bar_rail_gate_problem

ANTI_BOTTOM_SHORT_STRETCH_ATR = 3.0
ANTI_BOTTOM_SHORT_PULLBACK_ATR = 0.20
ANTI_BOTTOM_SHORT_POST_CLOSE_REBOUND_ATR = 0.50
ANTI_BOTTOM_SHORT_WICK_BODY_RATIO = 1.50
ANTI_BOTTOM_SHORT_BOUNCE_BODY_ATR = 0.50
ANTI_BOTTOM_SHORT_COOLDOWN_BARS = 3
ANTI_BOTTOM_SHORT_TREND_LOOKBACK_BARS = 3
ANTI_BOTTOM_SHORT_MIN_KC_DROP_ATR = 0.10

# 合法入場信號：三大純粹模式 (TREND_BREAKOUT, MA_CROSS_FAST, MOMENTUM_GUILLOTINE)
RULE_CODES = frozenset(
    [f"CLOSED_{rule}_{side}" for rule in "ABCDEF" for side in ("LONG", "SHORT")] +
    [f"CLOSED_TREND_BREAKOUT_{side}" for side in ("LONG", "SHORT")] +
    [f"CLOSED_MA_CROSS_FAST_{side}" for side in ("LONG", "SHORT")] +
    [f"CLOSED_MOMENTUM_GUILLOTINE_{side}" for side in ("LONG", "SHORT")]
)
# TREND_CRAWLING 已永久停用，嚴禁恢復。

def validate_channel_expansion(indicators: dict, side: str, rule: str, bypass_low_vol: bool = False) -> tuple[bool, str]:
    kc_upper = indicators['kc_upper']
    kc_lower = indicators['kc_lower']
    kc_middle = indicators['kc_middle']
    atr = indicators['atr'][-1]
    
    kc_upper_slope = kc_upper[-1] - kc_upper[-2]
    kc_lower_slope = kc_lower[-1] - kc_lower[-2]
    
    # 1. CK 狀態過濾：嚴禁在明確反向趨勢下開倉
    ck_status = indicators.get('ck_status', '')
    if "明確反向趨勢" in ck_status:
        return False, "BLOCKED_CK_REVERSE_TREND"

    if bypass_low_vol or rule in ('IGNITION', 'TREND_BREAKOUT'):
        return True, "BYPASS_CHANNEL_EXPANDING"

    # 【單邊張口放行】：強趨勢下豁免 (Upper - Lower) 全通道連續擴張限制
    if rule == 'TREND_CRAWLING':
        if side == "LONG":
            # 多頭單邊：上軌未急速收縮下墜即放行
            if kc_upper_slope > -0.0003:
                return True, "PASSED"
            else:
                return False, "MODE_B_WEAKENING (上軌塌陷中)"
        elif side == "SHORT":
            # 空頭單邊：下軌未急速收縮上翹即放行
            if kc_lower_slope < 0.0003:
                return True, "PASSED"
            else:
                return False, "MODE_B_WEAKENING (下軌塌陷中)"

    # 模式 A (IGNITION) 等，維持原全通道連續擴張與寬度門檻
    curr_width = kc_upper[-1] - kc_lower[-1]
    prev_width = kc_upper[-2] - kc_lower[-2]
    width_pct = curr_width / kc_middle[-1]
    
    MIN_VOLATILITY_RATIO = 1.5
    current_ratio = curr_width / atr if atr > 0 else 0
    if current_ratio < MIN_VOLATILITY_RATIO or width_pct < 0.010:
        return False, f"[BLOCKED] LOW_VOLATILITY: current_ratio={current_ratio:.2f}, threshold={MIN_VOLATILITY_RATIO}"
        
    is_channel_expanding = curr_width > prev_width
    if not is_channel_expanding:
        return False, "BLOCKED_CHANNEL_NOT_EXPANDING"

    # 單邊軌道發散審查
    if side == "LONG":
        if kc_upper[-1] <= kc_upper[-2] or kc_middle[-1] <= kc_middle[-2]:
            return False, "BLOCKED_UPPER_RAIL_NOT_RISING"
    elif side == "SHORT":
        if kc_lower[-1] >= kc_lower[-2] or kc_middle[-1] >= kc_middle[-2]:
            return False, "BLOCKED_LOWER_RAIL_NOT_FALLING"

    return True, "PASSED"


def validate_ma_alignment(frame, side: str, quote: float = None) -> tuple[bool, str]:
    """【均線相對位置的絕對限制】開倉閘門第一道底線：
    1. 多單（LONG）方向鐵律：
       - 硬性門檻：必須滿足 MA5 > MA15（MA5 位於 MA15 上方，呈黃金交叉或多頭排列）。
       - 收線確認：當根 1m K 線收盤價必須站穩在均線之上（close > ma5）。
       - 一票否決：若 MA5 <= MA15 或 close <= ma5，嚴禁開多單。
    2. 空單（SHORT）方向鐵律：
       - 硬性門檻：必須滿足 MA5 < MA15（MA5 位於 MA15 下方，呈死亡交叉或空頭排列）。
       - 收線確認：當根 1m K 線收盤價必須壓制在均線之下（close < ma5）。
       - 一票否決：若 MA5 >= MA15 或 close >= ma5，嚴禁開空單。
    """
    if frame is None or len(frame) < 1 or side not in ('LONG', 'SHORT'):
        return False, "REJECT: MA_ALIGNMENT_MISMATCH"
    try:
        curr = frame.iloc[-1]
        is_forming = ('is_closed' in curr and not bool(curr['is_closed'])) or (
            'is_closed' not in curr and quote is not None and quote != float(curr.get('close', quote))
        )
        if is_forming and quote is not None:
            close = float(quote)
            raw_close = float(curr.get('close', quote))
            ma5 = (
                float(curr['ma5']) + (close - raw_close) / 5.0
                if 'ma5' in curr and math.isfinite(float(curr['ma5']))
                else (float(frame['close'].tail(5).mean()) if len(frame) >= 5 else close)
            )
            ma15 = (
                float(curr['ma15']) + (close - raw_close) / 15.0
                if 'ma15' in curr and math.isfinite(float(curr['ma15']))
                else (float(frame['close'].tail(15).mean()) if len(frame) >= 15 else close)
            )
        else:
            close = float(curr['close']) if 'close' in curr else float(quote)
            ma5 = (
                float(curr['ma5'])
                if 'ma5' in curr and math.isfinite(float(curr['ma5']))
                else (float(frame['close'].tail(5).mean()) if len(frame) >= 5 else close)
            )
            ma15 = (
                float(curr['ma15'])
                if 'ma15' in curr and math.isfinite(float(curr['ma15']))
                else (float(frame['close'].tail(15).mean()) if len(frame) >= 15 else close)
            )

        if not (math.isfinite(ma5) and math.isfinite(ma15) and math.isfinite(close)):
            return False, "REJECT: MA_ALIGNMENT_MISMATCH"

        if side == 'LONG':
            if ma5 <= ma15 or close <= ma5:
                return False, "REJECT: MA_ALIGNMENT_MISMATCH"
        elif side == 'SHORT':
            if ma5 >= ma15 or close >= ma5:
                return False, "REJECT: MA_ALIGNMENT_MISMATCH"
        return True, "PASSED"
    except Exception:
        return False, "REJECT: MA_ALIGNMENT_MISMATCH"


def at_least(value, threshold):
    """Tolerate arithmetic rounding only at inclusive boundaries."""
    return value >= threshold or math.isclose(value, threshold, rel_tol=1e-12, abs_tol=0.)


def wick_exhaustion_problem(candle, side, quote=None, min_body_ratio=0.35):
    """Reject invalid or weak-body candles; the shared gate handles confirmed exhaustion."""
    if side not in (None, 'LONG', 'SHORT'):
        return 'BLOCKED_INVALID_ENTRY_SIDE'
    try:
        opening = float(candle['open'] if hasattr(candle, '__getitem__') else candle.open)
        raw_close = float(candle['close'] if hasattr(candle, '__getitem__') else candle.close)
        high = float(candle['high'] if hasattr(candle, '__getitem__') else candle.high)
        low = float(candle['low'] if hasattr(candle, '__getitem__') else candle.low)
        close = float(quote) if quote is not None else raw_close
        high, low = max(high, opening, close), min(low, opening, close)
        if (not all(math.isfinite(value) and value > 0
                    for value in (opening, close, high, low))
                or high <= low or low > min(opening, close)
                or high < max(opening, close)):
            return 'BLOCKED_INVALID_ENTRY_CANDLE'
        body = abs(close - opening)
        body_ratio = body / (high - low + 1e-9)
        if body_ratio < float(min_body_ratio):
            return 'REJECT_ENTRY: DOMINATED_BY_WICKS'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_INVALID_ENTRY_CANDLE'


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


def long_entry_trend_problem(closed):
    """Require a closed upper-rail break and sustained reversal evidence.

    MA15: two positive changes; price: two closes strictly above their
    midlines; CK: two positive changes of all three rails. Any one confirms.
    This gate applies to every LONG rule, including ignition exemptions.
    """
    try:
        rows = closed.iloc[-3:]
        keys = ['close', 'ma15', 'kc_upper', 'kc_middle', 'kc_lower']
        if len(rows) != 3:
            return 'BLOCKED_LONG_INVALID_TREND_DATA'
        values = rows[keys].astype(float)
        if not all(math.isfinite(v) and v > 0 for v in values.to_numpy().flat):
            return 'BLOCKED_LONG_INVALID_TREND_DATA'
        if not (values.kc_lower.lt(values.kc_middle) & values.kc_middle.lt(values.kc_upper)).all():
            return 'BLOCKED_LONG_INVALID_TREND_DATA'
        if values.close.iloc[-1] <= values.kc_upper.iloc[-1]:
            return 'BLOCKED_LONG_CLOSE_NOT_ABOVE_UPPER'
        ma15_up = bool(values.ma15.diff().iloc[1:].gt(0).all())
        two_above_middle = bool(values.close.iloc[-2:].gt(values.kc_middle.iloc[-2:]).all())
        ck_up = bool(values[['kc_upper', 'kc_middle', 'kc_lower']].diff().iloc[1:].gt(0).to_numpy().all())
        if not (ma15_up or two_above_middle or ck_up):
            return 'BLOCKED_LONG_REVERSAL_UNCONFIRMED'
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return 'BLOCKED_LONG_INVALID_TREND_DATA'
    return None


def short_entry_trend_problem(closed):
    """Require a closed lower-rail break and sustained reversal evidence.

    MA15: two negative changes; price: two closes strictly below their
    midlines; CK: two negative changes of all three rails. Any one confirms.
    This gate applies to every SHORT rule, mirroring LONG symmetrically.
    """
    try:
        rows = closed.iloc[-3:]
        keys = ['close', 'ma15', 'kc_upper', 'kc_middle', 'kc_lower']
        if len(rows) != 3:
            return 'BLOCKED_SHORT_INVALID_TREND_DATA'
        values = rows[keys].astype(float)
        if not all(math.isfinite(v) and v > 0 for v in values.to_numpy().flat):
            return 'BLOCKED_SHORT_INVALID_TREND_DATA'
        if not (values.kc_lower.lt(values.kc_middle) & values.kc_middle.lt(values.kc_upper)).all():
            return 'BLOCKED_SHORT_INVALID_TREND_DATA'
        if values.close.iloc[-1] >= values.kc_lower.iloc[-1]:
            return 'BLOCKED_SHORT_CLOSE_NOT_BELOW_LOWER'
        ma15_down = bool(values.ma15.diff().iloc[1:].lt(0).all())
        two_below_middle = bool(values.close.iloc[-2:].lt(values.kc_middle.iloc[-2:]).all())
        ck_down = bool(values[['kc_upper', 'kc_middle', 'kc_lower']].diff().iloc[1:].lt(0).to_numpy().all())
        if not (ma15_down or two_below_middle or ck_down):
            return 'BLOCKED_SHORT_REVERSAL_UNCONFIRMED'
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return 'BLOCKED_SHORT_INVALID_TREND_DATA'
    return None


def peak_trough_cross(closed, side):
    # Completely disabled PEAK_TROUGH_CROSS logic to prevent counter-trend tops/bottoms
    return None


def had_close(account, symbol):
    return any(t.get('symbol') == symbol and t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')
               for t in getattr(account, 'trades', []))


def short_kc_trend_problem(frame):
    """Require smoothed KC middle/lower rails to show a material down slope."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < ANTI_BOTTOM_SHORT_TREND_LOOKBACK_BARS:
            return 'BLOCKED_SHORT_KC_TREND_NOT_DOWN'
        rows = closed.tail(ANTI_BOTTOM_SHORT_TREND_LOOKBACK_BARS)
        middle = [float(value) for value in rows['kc_middle']]
        lower = [float(value) for value in rows['kc_lower']]
        atr = float(rows.iloc[-1]['atr'])
        if not all(math.isfinite(value) and value > 0 for value in middle + lower + [atr]):
            return 'BLOCKED_SHORT_KC_TREND_NOT_DOWN'
        middle_drop = middle[0] - middle[-1]
        if (middle[-1] >= middle[-2] or lower[-1] >= lower[-2]
                or middle_drop < ANTI_BOTTOM_SHORT_MIN_KC_DROP_ATR * atr):
            return 'BLOCKED_SHORT_KC_TREND_NOT_DOWN'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_SHORT_KC_TREND_NOT_DOWN'


def anti_bottom_short_problem(
    frame, price=None, *, account=None, symbol='',
    allow_confirmed_rail_break=False,
):
    """Block exhausted shorts until a measured rebound is rejected near structure."""
    try:
        if frame is None or frame.empty:
            return 'BLOCKED_SHORT_ANTI_BOTTOM_INVALID_DATA'
        closed = closed_entry_candles(frame)
        live = frame.iloc[-1]
        forming = ('is_closed' in frame.columns and not bool(live['is_closed']))
        if forming:
            signal = live
            history = closed.tail(ANTI_BOTTOM_SHORT_COOLDOWN_BARS)
            raw_close = float(live['close'])
            close = float(price) if price is not None else raw_close
            high = max(float(live['high']), close)
            low = min(float(live['low']), close)
        else:
            if closed.empty:
                return 'BLOCKED_SHORT_ANTI_BOTTOM_INVALID_DATA'
            signal = closed.iloc[-1]
            history = closed.iloc[:-1].tail(ANTI_BOTTOM_SHORT_COOLDOWN_BARS)
            close = float(price) if price is not None else float(signal['close'])
            high = max(float(signal['high']), close)
            low = min(float(signal['low']), close)
        atr = float(signal['atr'])
        lower = float(signal['kc_lower'])
        middle = float(signal['kc_middle'])
        ma3 = float(signal['ma3'])
        opening = float(signal['open'])
        values = (close, atr, lower, middle, ma3, opening, high, low)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or atr <= 0 or lower >= middle):
            return 'BLOCKED_SHORT_ANTI_BOTTOM_INVALID_DATA'

        if not allow_confirmed_rail_break:
            trend_problem = short_kc_trend_problem(frame)
            if trend_problem:
                return trend_problem

            if close < lower - ANTI_BOTTOM_SHORT_STRETCH_ATR * atr:
                return 'BLOCKED_SHORT_KC_LOWER_STRETCH'

        # Do not sell directly into a recent rejection wick or a forceful
        # bullish reclaim through MA3. Three completed bars provide cooldown.
        for _, bar in history.iterrows():
            bar_open, bar_close, bar_high, bar_low, bar_atr, bar_ma3 = (
                float(bar[key]) for key in ('open', 'close', 'high', 'low', 'atr', 'ma3')
            )
            vals = (bar_open, bar_close, bar_high, bar_low, bar_atr, bar_ma3)
            if (not all(math.isfinite(value) and value > 0 for value in vals)
                    or bar_high < max(bar_open, bar_close)
                    or bar_low > min(bar_open, bar_close)):
                return 'BLOCKED_SHORT_ANTI_BOTTOM_INVALID_DATA'
            body = abs(bar_close - bar_open)
            lower_wick = min(bar_open, bar_close) - bar_low
            long_lower_wick = lower_wick > ANTI_BOTTOM_SHORT_WICK_BODY_RATIO * body
            strong_ma3_reclaim = (
                bar_close > bar_open
                and bar_open < bar_ma3 < bar_close
                and body >= ANTI_BOTTOM_SHORT_BOUNCE_BODY_ATR * bar_atr
            )
            if ((long_lower_wick and not allow_confirmed_rail_break)
                    or strong_ma3_reclaim):
                return 'BLOCKED_SHORT_EXHAUSTION_COOLDOWN'

        if close < lower:
            # A short below the band is eligible only after this bar actually
            # tested MA3/KC support from below and closed bearish back under it.
            red_close = close < opening
            targets = (ma3, lower, middle)
            same_bar_rejection = red_close and any(
                high >= target - ANTI_BOTTOM_SHORT_PULLBACK_ATR * atr
                and high - close >= ANTI_BOTTOM_SHORT_PULLBACK_ATR * atr
                and close < target
                for target in targets
            )
            prior_pullback = False
            for _, bar in history.iterrows():
                bar_open, bar_close, bar_high = (
                    float(bar[key]) for key in ('open', 'close', 'high')
                )
                if bar_close > bar_open and any(
                    bar_high >= target - ANTI_BOTTOM_SHORT_PULLBACK_ATR * atr
                    and bar_close > target - ANTI_BOTTOM_SHORT_PULLBACK_ATR * atr
                    for target in targets
                ):
                    prior_pullback = True
                    break
            rejection = red_close and any(close < target for target in targets) and (
                same_bar_rejection or prior_pullback
            )
            if not rejection and not allow_confirmed_rail_break:
                return 'BLOCKED_SHORT_WAIT_PULLBACK_REJECTION'

        signal_body = abs(close - opening)
        signal_lower_wick = min(opening, close) - low
        if (signal_lower_wick > ANTI_BOTTOM_SHORT_WICK_BODY_RATIO * signal_body
                and not allow_confirmed_rail_break):
            return 'BLOCKED_SHORT_EXHAUSTION_COOLDOWN'

        # A matched recent short close must first see a real 0.5 ATR rebound
        # from the post-close low; a fresh low alone cannot authorize a relay.
        if account is not None and symbol:
            relevant = [
                trade for trade in getattr(account, 'trades', [])
                if trade.get('symbol') == symbol
                and trade.get('action') in ('OPEN_SHORT', 'CLOSE_SHORT')
            ]
            latest = max(relevant, key=lambda trade: float(trade.get('id') or 0.)) if relevant else None
            if latest and latest.get('action') == 'CLOSE_SHORT' and latest.get('status') == 'CLOSED':
                close_ms = float(latest.get('id') or 0.)
                close_price = float(latest.get('price') or 0.)
                if not all(math.isfinite(value) and value > 0 for value in (close_ms, close_price)):
                    return 'BLOCKED_SHORT_POST_CLOSE_NO_PULLBACK'
                first_after_close = math.floor(close_ms / 60000.) * 60000. + 60000.
                post_close_rows = frame.loc[frame['timestamp'].astype(float) >= first_after_close]
                if post_close_rows.empty:
                    return 'BLOCKED_SHORT_POST_CLOSE_NO_PULLBACK'
                lows = [float(value) for value in post_close_rows['low']]
                if not all(math.isfinite(value) and value > 0 for value in lows):
                    return 'BLOCKED_SHORT_ANTI_BOTTOM_INVALID_DATA'
                post_close_low = min(lows)
                if close - post_close_low < ANTI_BOTTOM_SHORT_POST_CLOSE_REBOUND_ATR * atr:
                    return 'BLOCKED_SHORT_POST_CLOSE_NO_PULLBACK'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_SHORT_ANTI_BOTTOM_INVALID_DATA'


def evaluate_closed_entry(frame, side, price=None, *, after_close=False, account=None, symbol=''):
    wait = lambda reason: (False, reason, dict(action='WAIT', side=side, reason=reason))

    closed = confirmed(frame)
    if closed is None or closed.empty:
        return wait("NOT_READY")

    # 【均線相對位置的絕對限制】第一道開倉閘門底線
    passed, reason = validate_ma_alignment(frame, side, price)
    if not passed:
        return wait(reason)

    quote = price if price is not None else float(frame.iloc[-1]['close'])
    from core.gates.strict_cross_kc_gate import MemeSharedProtectionGate
    shared_ok, shared_reason = MemeSharedProtectionGate.evaluate(frame, side, quote, symbol=symbol)
    if not shared_ok:
        return wait(shared_reason)

    c = closed.iloc[-1]
    prev_c = closed.iloc[-2] if len(closed) >= 2 else c
    c_open = float(c.open)
    c_high = float(c.high)
    c_low = float(c.low)
    c_close = float(c.close)
    atr = float(c.get('atr', prev_c.get('atr', 1.0)))
    if atr <= 0:
        atr = 1.0

    curr_ma5 = float(c.get('ma5', c_close))
    curr_ma15 = float(c.get('ma15', c_close))
    kc_upper = float(c.get('kc_upper', 0.0))
    kc_lower = float(c.get('kc_lower', 0.0))
    kc_middle = float(c.get('kc_middle', (kc_upper + kc_lower) / 2.0 if kc_upper > kc_lower else 0.0))
    prev_kc_middle = float(prev_c.get('kc_middle', kc_middle))

    candle_range = c_high - c_low
    body = abs(c_close - c_open)
    body_ratio = body / candle_range if candle_range > 1e-9 else 1.0

    from core.services.entry_contract import _PENDING_WICK_REENTRY_STATE
    sym_key = symbol or '__default__'
    pending_state = _PENDING_WICK_REENTRY_STATE.get(sym_key)
    curr_stamp = float(c.timestamp)
    is_retest_hit = False

    # ══════════════════════════════════════════════════════════════════════
    # 【移至適合 K 線的延續開倉（Eligible Re-entry Conditions）】
    # ══════════════════════════════════════════════════════════════════════
    if pending_state and curr_stamp > pending_state['bar_id']:
        bars_diff = int(round((curr_stamp - pending_state['bar_id']) / 60000))
        p_side = pending_state['side']
        trend_broken = False
        if p_side == 'LONG' and (kc_middle < prev_kc_middle - 1e-6 or curr_ma5 < curr_ma15):
            trend_broken = True
        elif p_side == 'SHORT' and (kc_middle > prev_kc_middle + 1e-6 or curr_ma5 > curr_ma15):
            trend_broken = True

        if bars_diff > 3 or trend_broken:
            _PENDING_WICK_REENTRY_STATE.pop(sym_key, None)
        elif p_side == side:
            if side == 'LONG':
                upper_wick = c_high - max(c_open, c_close)
                pullback_held = (c_low <= curr_ma5 or c_low <= curr_ma15) and (c_close >= curr_ma5 and c_close >= curr_ma15 and c_close > c_open)
                breakout_reclaim = (body >= 0.5 * atr and upper_wick <= 0.3 * body and c_close > c_open and c_close > pending_state['extreme_price'])
                if pullback_held or breakout_reclaim:
                    _PENDING_WICK_REENTRY_STATE.pop(sym_key, None)
                    is_retest_hit = True
                    selected_rule = 'PENDING_RETEST_ENTRY'
            elif side == 'SHORT':
                lower_wick = min(c_open, c_close) - c_low
                pullback_rejected = (c_high >= curr_ma5 or c_high >= curr_ma15) and (c_close <= curr_ma5 and c_close <= curr_ma15 and c_close < c_open)
                breakout_reclaim = (body >= 0.5 * atr and lower_wick <= 0.3 * body and c_close < c_open and c_close < pending_state['extreme_price'])
                if pullback_rejected or breakout_reclaim:
                    _PENDING_WICK_REENTRY_STATE.pop(sym_key, None)
                    is_retest_hit = True
                    selected_rule = 'PENDING_RETEST_ENTRY'

    is_mode1_hit = False
    is_mode2_hit = False
    if not is_retest_hit:
        selected_rule = None

        # ══════════════════════════════════════════════════════════════════════
        # 【模式一：一般 KC 收線破軌開倉（Breakout Close Entry - 抓單邊趨勢發動）】
        # ══════════════════════════════════════════════════════════════════════
        if side == 'LONG':
            kc_trend_str = str(c.get('kc_trend', '')).strip().upper()
            mode1_kc_trend_ok = (kc_trend_str == 'UP') or (not kc_trend_str and kc_middle > prev_kc_middle)
            if (
                c_close > kc_upper
                and c_close > c_open
                and body >= 0.4 * atr
                and mode1_kc_trend_ok
                and curr_ma5 > curr_ma15
            ):
                is_mode1_hit = True
                selected_rule = 'TREND_BREAKOUT'
        elif side == 'SHORT':
            kc_trend_str = str(c.get('kc_trend', '')).strip().upper()
            mode1_kc_trend_ok = (kc_trend_str == 'DOWN') or (not kc_trend_str and kc_middle < prev_kc_middle)
            if (
                c_close < kc_lower
                and c_close < c_open
                and body >= 0.4 * atr
                and mode1_kc_trend_ok
                and curr_ma5 < curr_ma15
            ):
                is_mode1_hit = True
                selected_rule = 'TREND_BREAKOUT'

        # ══════════════════════════════════════════════════════════════════════
        # 【模式二：MA5 / MA15 交叉開倉（Strict Cross Entry - 抓順勢真轉折）】
        # ══════════════════════════════════════════════════════════════════════
        is_mode_cross_hit = False
        if not is_mode1_hit:
            from core.gates.strict_cross_kc_gate import has_recent_golden_cross, has_recent_death_cross, StrictCrossAndKcTrendGate
            kc_trend_str = str(c.get('kc_trend', '')).strip().upper()
            if side == 'LONG':
                kc_up = (kc_trend_str == 'UP') or (not kc_trend_str and kc_middle > prev_kc_middle)
                ma5_prev_c = float(prev_c.get('ma5', curr_ma5))
                recent_gc = has_recent_golden_cross(closed, max_lookback=3, curr_ma5=curr_ma5, curr_ma15=curr_ma15)
                # 勾頭檢查
                if recent_gc and curr_ma5 <= ma5_prev_c:
                    return wait("REJECT_ENTRY: MA5_HOOKING_DOWN_AFTER_CROSS")
                if curr_ma5 <= curr_ma15 and recent_gc:
                    return wait("REJECT_ENTRY: NO_GOLDEN_CROSS_MA5_BELOW_MA15")
                ma5_rising = curr_ma5 > ma5_prev_c
                price_above_ma5 = (c_close > curr_ma5)
                if (
                    kc_up
                    and recent_gc
                    and curr_ma5 > curr_ma15
                    and ma5_rising
                    and price_above_ma5
                ):
                    is_mode_cross_hit = True
                    selected_rule = 'MA_CROSS_FAST'
            elif side == 'SHORT':
                kc_down = (kc_trend_str == 'DOWN') or (not kc_trend_str and kc_middle < prev_kc_middle)
                ma5_prev_c = float(prev_c.get('ma5', curr_ma5))
                # 鐵律：MA5 仍在 MA15 上方嚴禁開空
                if curr_ma5 >= curr_ma15:
                    # 檢測是否有短線勾頭向下欲嘗試開空
                    if curr_ma5 < ma5_prev_c:
                        return wait("REJECT_ENTRY: NO_DEATH_CROSS_MA5_ABOVE_MA15")
                recent_dc = has_recent_death_cross(closed, max_lookback=3, curr_ma5=curr_ma5, curr_ma15=curr_ma15)
                if recent_dc and curr_ma5 >= ma5_prev_c:
                    return wait("REJECT_ENTRY: MA5_HOOKING_UP_AFTER_CROSS")
                ma5_falling = curr_ma5 < ma5_prev_c
                price_below_ma5 = (c_close < curr_ma5)
                if (
                    kc_down
                    and recent_dc
                    and curr_ma5 < curr_ma15
                    and ma5_falling
                    and price_below_ma5
                ):
                    is_mode_cross_hit = True
                    selected_rule = 'MA_CROSS_FAST'

        # ══════════════════════════════════════════════════════════════════════
        # 【模式三：斷頭開倉（Decisive Momentum Guillotine Entry - 抓極端單邊爆發）】
        # ══════════════════════════════════════════════════════════════════════
        if not is_mode1_hit and not is_mode_cross_hit:
            if side == 'LONG':
                pierced_upward = (
                    (c_open <= kc_middle and c_close > kc_middle)
                    or (c_open <= curr_ma15 and c_close > curr_ma15)
                    or (c_open <= curr_ma5 and c_close > curr_ma5)
                )
                if (
                    body >= 0.85 * atr
                    and c_close > c_open
                    and body_ratio >= 0.70
                    and pierced_upward
                ):
                    is_mode2_hit = True
                    selected_rule = 'MOMENTUM_GUILLOTINE'
            elif side == 'SHORT':
                pierced_downward = (
                    (c_open >= kc_middle and c_close < kc_middle)
                    or (c_open >= curr_ma15 and c_close < curr_ma15)
                    or (c_open >= curr_ma5 and c_close < curr_ma5)
                )
                if (
                    body >= 0.85 * atr
                    and c_close < c_open
                    and body_ratio >= 0.70
                    and pierced_downward
                ):
                    is_mode2_hit = True
                    selected_rule = 'MOMENTUM_GUILLOTINE'

        if not is_mode1_hit and not is_mode_cross_hit and not is_mode2_hit:
            return wait('WAIT_BREAKOUT_OR_GUILLOTINE_TRIGGER')


    # ══════════════════════════════════════════════════════════════════════
    # 【前置硬檢核：MemeSharedProtectionGate 與 StrictCrossAndKcTrendGate】
    # ══════════════════════════════════════════════════════════════════════
    from core.gates.strict_cross_kc_gate import StrictCrossAndKcTrendGate, MemeSharedProtectionGate
    is_breakout_mode = selected_rule in ('TREND_BREAKOUT', 'MOMENTUM_GUILLOTINE')
    shared_ok, shared_reason = MemeSharedProtectionGate.evaluate(
        frame, side, price, symbol=symbol, bypass_spatial_check=is_breakout_mode
    )
    if not shared_ok:
        return wait(shared_reason)

    if selected_rule == 'MA_CROSS_FAST':
        gate_ok, gate_reason = StrictCrossAndKcTrendGate.evaluate(
            frame, side, price, bypass_spatial_check=False
        )
        if not gate_ok:
            return wait(gate_reason)

    code = f'CLOSED_{selected_rule}_{side}'
    return True, code, dict(
        action='ENTER', side=side, reason=code, rule=selected_rule,
        entry_type=selected_rule, entry_atr=atr, is_breakout=is_breakout_mode,
        confirmation_bar_id=float(c.timestamp), close_price=float(c.close),
        pending_signal_id=f"{selected_rule}:{side}:{int(c.timestamp)}",
        candidate_bar_id=float(c.timestamp),
        bypass_spatial_check=is_breakout_mode,  # 空間豁免：凡收線破軌/貫穿直接標記 bypass_spatial_check = True
    )



def check_streamlined_entry_signal(df, side, live_price, position_status, **kwargs):
    if position_status != 'NO_POSITION':
        return False, 'WAIT_EXISTING_POSITION', {'action': 'WAIT'}

    if df is None or df.empty:
        return False, 'BLOCKED_INVALID_ENTRY_CANDLE', {'action': 'WAIT'}
    live = df.iloc[-1]
    live_quote = live_price if not bool(live.get('is_closed', True)) else None
    wick_problem = wick_exhaustion_problem(live, side, quote=live_quote)
    if wick_problem:
        return False, wick_problem, {'action': 'WAIT', 'reason': wick_problem}

    from core.gates.pipeline import pipeline
    diagnostics = {}
    decision = pipeline.authorize(
        None, df, live_price, symbol=kwargs.get('symbol', ''),
        requested_side=side, account=kwargs.get('account'),
        diagnostics=diagnostics,
    )
    if decision is None:
        return False, diagnostics.get('reason', 'WAIT_PIPELINE_TRIGGER'), {'action': 'WAIT'}
    if decision.get('side') != side:
        return False, 'BLOCKED_PIPELINE_SIDE_MISMATCH', {'action': 'WAIT'}
    return True, decision['type'], {'action': 'ENTER', **decision}


def check_ma_cross_entry(df, live_price=None):
    """【清理廢棄邏輯】：MA5 / MA15 交叉敏捷開倉已永久廢除，僅保留純粹進場雙引擎。"""
    return None


class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if kwargs.get('existing_pos'):
            return False, 'WAIT_EXISTING_POSITION', {'action': 'WAIT'}
        from core.services.strategies.three_mode_entry_engine import (
            evaluate_entry_signal,
        )
        diagnostics = {}
        engine = kwargs.get("engine")
        decision = evaluate_entry_signal(
            frame,
            position=kwargs.get("position"),
            account=getattr(engine, "account", None),
            symbol=kwargs.get("symbol", ""),
            quote=price,
            requested_side=side,
            diagnostics=diagnostics,
        )
        if decision is None:
            reason = diagnostics.get("reason", "WAIT_NO_THREE_MODE_SETUP")
            return False, reason, {"action": "WAIT", "reason": reason}
        return True, decision["type"], decision

        # 【均線相對位置的絕對限制】第一道開倉閘門底線
        passed, reason = validate_ma_alignment(frame, side, price)
        if not passed:
            return False, reason, {'action': 'WAIT', 'reason': reason}

        if frame is None or frame.empty:
            return False, 'BLOCKED_INVALID_ENTRY_CANDLE', {'action': 'WAIT'}
        live = frame.iloc[-1]
        live_quote = price if not bool(live.get('is_closed', True)) else None
        wick_problem = wick_exhaustion_problem(live, side, quote=live_quote)
        if wick_problem:
            return False, wick_problem, {'action': 'WAIT', 'reason': wick_problem}

        from core.gates.pipeline import ma5_ma15_cross_chop_problem
        cross_chop_problem = ma5_ma15_cross_chop_problem(
            frame, price, symbol=kwargs.get('symbol', ''), log_result=True,
            side=side,
        )
        if cross_chop_problem:
            return False, cross_chop_problem, {'action': 'WAIT'}

        engine = kwargs.get('engine')
        if engine is not None:
            from core.gates.pipeline import pipeline
            peak_flip = pipeline.authorize(
                None, frame, price, symbol=kwargs.get('symbol', ''),
                requested_side=side, account=engine.account,
            )
            if (peak_flip is not None
                    and peak_flip.get('type') == 'AUTHORIZED_BY_PEAK_FLIP_SHORT'
                    and peak_flip.get('side') == side):
                return True, peak_flip['type'], peak_flip

            if getattr(engine, '_market_crash_entries_paused', lambda x: False)(None):
                return False, "BTC_FLASH_CRASH_COOLDOWN", {'action': 'WAIT'}

            cooldown_until = getattr(engine, '_market_crash_entry_cooldown_until', 0.0)
            if cooldown_until > 0 and frame is not None and not frame.empty:
                try:
                    after_close = (had_close(engine.account, kwargs.get('symbol', ''))
                                   if 'had_close' in globals() else kwargs.get('after_close', False))
                    closed = frame[:-1] if not after_close else frame
                    if not closed.empty:
                        conf_ts = float(closed.iloc[-1].get('timestamp', 0))
                        if conf_ts > 0 and conf_ts <= cooldown_until * 1000:
                            return False, "STALE_CRASH_SIGNAL_REJECTED", {'action': 'WAIT'}
                except Exception:
                    pass

        from core.gates.pipeline import pipeline
        diagnostics = {}
        decision = pipeline.authorize(
            None, frame, price, symbol=kwargs.get('symbol', ''),
            requested_side=side,
            account=getattr(engine, 'account', None),
            diagnostics=diagnostics,
        )
        if decision is None:
            reason = diagnostics.get('reason', 'WAIT_PIPELINE_TRIGGER')
            return False, reason, {'action': 'WAIT'}
        if decision.get('side') != side:
            return False, 'BLOCKED_PIPELINE_SIDE_MISMATCH', {'action': 'WAIT'}
        return True, decision['type'], decision
