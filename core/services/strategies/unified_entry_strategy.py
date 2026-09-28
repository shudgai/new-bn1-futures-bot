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

RULE_CODES = frozenset(
    [f"CLOSED_{rule}_{side}" for rule in "ABCDEF" for side in ("LONG", "SHORT")] +
    [f"CLOSED_IGNITION_{side}" for side in ("LONG", "SHORT")] +
    [f"CLOSED_TREND_CRAWLING_{side}" for side in ("LONG", "SHORT")] +
    [f"CLOSED_TREND_BREAKOUT_{side}" for side in ("LONG", "SHORT")] +
    [f"CLOSED_PEAK_TROUGH_CROSS_{side}" for side in ("LONG", "SHORT")]
)

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


def peak_trough_cross(closed, side):
    """Eight preceding closed bars establish the touch and structural stop."""
    if len(closed) < 9:
        return None
    try:
        rows = closed.iloc[-9:]
        keys = ['timestamp', 'open', 'high', 'low', 'close', 'kc_upper', 'kc_middle', 'kc_lower']
        v = rows[keys].astype(float)
        if not all(math.isfinite(x) and x > 0 for x in v.to_numpy().flat):
            return None
        if not v.timestamp.diff().iloc[1:].eq(60000).all():
            return None
        if not ((v.low <= v[['open', 'close']].min(axis=1)) &
                (v.high >= v[['open', 'close']].max(axis=1)) &
                (v.kc_lower < v.kc_middle) & (v.kc_middle < v.kc_upper)).all():
            return None
        history = rows.iloc[:-1]
        previous, current = rows.iloc[-2], rows.iloc[-1]
        sign = 1 if side == 'LONG' else -1
        previous_gap = sign * (float(previous.ma3)-float(previous.ma15))
        current_gap = sign * (float(current.ma3)-float(current.ma15))
        span = float(current.high)-float(current.low)
        body = sign * (float(current.close)-float(current.open))
        if not (previous_gap <= 0 < current_gap and body > 0 and span > 0
                and at_least(body/span, .4)):
            return None
        if side == 'LONG':
            touched = (history.low <= history.kc_lower).any()
            room = current.close <= current.kc_upper
            stop = float(history.low.min())
        else:
            touched = (history.high >= history.kc_upper).any()
            room = current.close >= current.kc_lower
            stop = float(history.high.max())
        if not touched or not room or sign*(float(current.close)-stop) <= 0:
            return None
        code = f'CLOSED_PEAK_TROUGH_CROSS_{side}'
        return dict(action='ENTER', side=side, reason=code, rule='PEAK_TROUGH_CROSS',
                    entry_type='PEAK_TROUGH_CROSS', entry_atr=float(current.atr),
                    is_breakout=False, confirmation_bar_id=float(current.timestamp),
                    close_price=float(current.close), initial_sl=stop,
                    peak_trough_lookback=8)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
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

    early = peak_trough_cross(closed, side)
    if early is not None:
        return True, early['reason'], early

    if side == 'LONG':
        problem = long_entry_trend_problem(closed)
        if problem:
            return wait(problem)

    c0, c1, c = (closed.iloc[i] for i in (-3, -2, -1))
    sign = 1 if side == 'LONG' else -1

    # ── 基礎數值 ─────────────────────────────────────────────────
    # The caller may provide either closed-only rows or a live tail. Always
    # derive entry CK from the same last two completed rows.
    previous = closed.iloc[-2]
    current = closed.iloc[-1]
    ck = None
    if current.kc_middle > previous.kc_middle and current.kc_upper >= previous.kc_upper:
        ck = 'LONG'
    elif current.kc_middle < previous.kc_middle and current.kc_lower <= previous.kc_lower:
        ck = 'SHORT'
    ck_opposing = 'SHORT' if side == 'LONG' else 'LONG'
    if ck == side:
        ck_status = '連續同向'
    elif ck == ck_opposing:
        ck_status = '明確反向趨勢'
    else:
        ck_status = '尚未連續同向 (非反向)'
    
    indicators = {
        'kc_upper': [float(c0.kc_upper), float(c1.kc_upper), float(c.kc_upper)],
        'kc_lower': [float(c0.kc_lower), float(c1.kc_lower), float(c.kc_lower)],
        'kc_middle': [float(c0.kc_middle), float(c1.kc_middle), float(c.kc_middle)],
        'atr': [float(c0.atr), float(c1.atr), float(c.atr)],
        'ck_status': ck_status
    }
        
    # 開倉觸發條件
    atr = float(c.atr)
    rule = None
    
    c_open = float(c.open)
    c_high = float(c.high)
    c_low = float(c.low)
    c_close = float(c.close)
    candle_range = c_high - c_low
    candle_body = abs(c_close - c_open)
    body_ratio = 1.0 if candle_range <= 1e-9 else candle_body / candle_range

    is_outside = False
    fast_crawling = False
    
    if side == "LONG":
        ma_aligned = float(c.ma3) > float(c.ma15)
        body = float(c.close) - float(c.open)
        upper_wick = float(c.high) - float(c.close)
        is_full_body = at_least(body, 0.55 * atr) and upper_wick < body * 0.8
        is_outside = float(c.close) > float(c.kc_upper)
        
        if ma_aligned and is_full_body and is_outside:
            rule = 'IGNITION'
        elif (ma_aligned and is_outside and len(closed) >= 6
              and c_close > float(closed['high'].iloc[-6:-1].max())):
            rule = 'TREND_BREAKOUT'
        else:
            # 模式 B：慢牛沿軌推進 (TREND_CRAWLING)
            # 1. 連續 3 根 ma3 > ma15 且 ma15 > kc_middle
            resonance = (
                float(c0.ma3) > float(c0.ma15) and float(c0.ma15) > float(c0.kc_middle) and
                float(c1.ma3) > float(c1.ma15) and float(c1.ma15) > float(c1.kc_middle) and
                float(c.ma3) > float(c.ma15) and float(c.ma15) > float(c.kc_middle)
            )
            # 2. ma15 斜率連續向上
            ma15_rising = float(c.ma15) > float(c1.ma15) and float(c1.ma15) > float(c0.ma15)
            # 3. 連續 2 根收盤價高於 KC 上軌
            crawling_outside = float(c.close) > float(c.kc_upper) and float(c1.close) > float(c1.kc_upper)
            
            # 加速啟動：當根在外軌，實體 >= 0.55 ATR 且均線發散
            fast_crawling = is_outside and at_least(body, 0.55 * atr) and float(c.ma3) > float(c.ma15)
            
            if (resonance and ma15_rising and crawling_outside) or fast_crawling:
                rule = 'TREND_CRAWLING'
            elif ma_aligned and is_outside and len(closed) >= 6:
                recent_high = float(closed['high'].iloc[-6:-1].max())
                if float(c.close) > recent_high:
                    rule = 'TREND_BREAKOUT'
                
    else:
        ma_aligned = float(c.ma3) < float(c.ma15)
        body = float(c.open) - float(c.close)
        lower_wick = float(c.close) - float(c.low)
        is_full_body = at_least(body, 0.55 * atr) and lower_wick < body * 0.8
        is_outside = float(c.close) < float(c.kc_lower)
        
        if ma_aligned and is_full_body and is_outside:
            rule = 'IGNITION'
        elif (ma_aligned and is_outside and len(closed) >= 6
              and c_close < float(closed['low'].iloc[-6:-1].min())):
            rule = 'TREND_BREAKOUT'
        else:
            # 模式 B：慢熊沿軌推進 (TREND_CRAWLING)
            # 1. 連續 3 根 ma3 < ma15 且 ma15 < kc_middle
            resonance = (
                float(c0.ma3) < float(c0.ma15) and float(c0.ma15) < float(c0.kc_middle) and
                float(c1.ma3) < float(c1.ma15) and float(c1.ma15) < float(c1.kc_middle) and
                float(c.ma3) < float(c.ma15) and float(c.ma15) < float(c.kc_middle)
            )
            # 2. ma15 斜率連續向下
            ma15_falling = float(c.ma15) < float(c1.ma15) and float(c1.ma15) < float(c0.ma15)
            # 3. 連續 2 根收盤價低於 KC 下軌
            crawling_outside = float(c.close) < float(c.kc_lower) and float(c1.close) < float(c1.kc_lower)
            
            # 加速啟動：當根在外軌，實體 >= 0.55 ATR 且均線發散
            fast_crawling = is_outside and at_least(body, 0.55 * atr) and float(c.ma3) < float(c.ma15)
            
            if (resonance and ma15_falling and crawling_outside) or fast_crawling:
                rule = 'TREND_CRAWLING'
            elif ma_aligned and is_outside and len(closed) >= 6:
                recent_low = float(closed['low'].iloc[-6:-1].min())
                if float(c.close) < recent_low:
                    rule = 'TREND_BREAKOUT'
            
    if sign * (c_close - c_open) <= 0:
        return wait('BLOCKED_OPPOSITE_CLOSED_BODY')

    if rule is None:
        return wait('WAIT_NEW_A_TO_E_TRIGGER')

    # IGNITION / TREND_BREAKOUT 信號保證動能，豁免滯後 MA3 斜率
    if rule not in ('IGNITION', 'TREND_BREAKOUT'):
        problem = ma3_entry_problem(closed, side)
        if problem:
            return wait(problem)

    bypass_low_vol = is_outside and (body_ratio >= 0.4 or fast_crawling)

    # 【優化】：判斷出 rule 後再進行通道審查，以豁免模式 B 的硬性限制
    passed, reason = validate_channel_expansion(indicators, side, rule, bypass_low_vol=bypass_low_vol)
    if not passed:
        return wait(reason)

    # 【長影線插針防護 (Pin Bar Rejection Filter)】
    if rule in ('TREND_CRAWLING', 'TREND_BREAKOUT'):
        # 1. 當根 K 棒防護：明顯下影線/上影線 (> 1.5倍實體)
        if side == 'SHORT':
            lower_wick = float(c.close) - float(c.low)
            if lower_wick > 1.5 * candle_body:
                return wait(f"PIN_BAR_DETECTED (當根長下影線誘空, wick={lower_wick:.5f} > 1.5*body={1.5*candle_body:.5f})")
        else:
            upper_wick = float(c.high) - float(c.close)
            if upper_wick > 1.5 * candle_body:
                return wait(f"PIN_BAR_DETECTED (當根長上影線誘多, wick={upper_wick:.5f} > 1.5*body={1.5*candle_body:.5f})")
                
        # 2. 前根 K 棒防護：已在外側插出長影線
        prev_body = abs(float(c1.close) - float(c1.open))
        if side == 'SHORT':
            prev_lower_wick = min(float(c1.close), float(c1.open)) - float(c1.low)
            if float(c1.low) < float(c1.kc_lower) and prev_lower_wick > 1.5 * prev_body:
                return wait("PREV_PIN_BAR_REJECTION (前根K棒已在下軌外插出長下影線，嚴禁追空)")
        else:
            prev_upper_wick = float(c1.high) - max(float(c1.close), float(c1.open))
            if float(c1.high) > float(c1.kc_upper) and prev_upper_wick > 1.5 * prev_body:
                return wait("PREV_PIN_BAR_REJECTION (前根K棒已在上軌外插出長上影線，嚴禁追多)")
                
        if body_ratio < 0.4:
            return wait(f"PIN_BAR_DETECTED (實體佔比過小, ratio={body_ratio:.2f})")

    code = f'CLOSED_{rule}_{side}'
    return True, code, dict(
        action='ENTER', side=side, reason=code, rule=rule,
        entry_type=rule, entry_atr=atr, is_breakout=True,
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
