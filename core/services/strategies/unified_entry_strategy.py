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

# 合法入場信號：只保留外軌起爆 (IGNITION) 與兩根破軌確認 (TREND_BREAKOUT)
RULE_CODES = frozenset(
    [f"CLOSED_{rule}_{side}" for rule in "ABCDEF" for side in ("LONG", "SHORT")] +
    [f"CLOSED_IGNITION_{side}" for side in ("LONG", "SHORT")] +
    [f"CLOSED_TREND_BREAKOUT_{side}" for side in ("LONG", "SHORT")]
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
    # Completely disabled PEAK_TROUGH_CROSS logic to prevent counter-trend tops/bottoms
    return None


def had_close(account, symbol):
    return any(t.get('symbol') == symbol and t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')
               for t in getattr(account, 'trades', []))


def evaluate_closed_entry(frame, side, *, after_close=False):
    wait = lambda reason: (False, reason, dict(action='WAIT', side=side, reason=reason))
    

    closed = confirmed(frame)
    if closed is None or closed.empty:
        return wait("NOT_READY")
        
    c = closed.iloc[-1]
    
    # 嚴格校驗已收線 K 棒的真實顏色與實體 (絕對防範由綠翻紅)
    bar_close = float(c.close)
    bar_open = float(c.open)
    if side == 'LONG':
        if bar_close <= bar_open:
            return wait("REJECT_LONG_CANDLE_IS_RED")  # 收紅 K 絕對禁止開多
        if bar_close <= float(c.kc_upper):
            return wait("REJECT_LONG_CLOSE_INSIDE_KC") # 沒實質站上外軌絕對禁止開多
    elif side == 'SHORT':
        if bar_close >= bar_open:
            return wait("REJECT_SHORT_CANDLE_IS_GREEN")  # 收綠 K 絕對禁止開空
        if bar_close >= float(c.kc_lower):
            return wait("REJECT_SHORT_CLOSE_INSIDE_KC") # 沒實質跌破外軌絕對禁止開空
    if side not in ('LONG', 'SHORT'):
        return wait('INVALID_SIDE')

    if closed is None:
        return wait('WAIT_VALID_CLOSED_1M_DATA')

    # 位置物理鐵律 (Price vs KC Mid) & 趨勢方向鐵律
    c1, c = closed.iloc[-2], closed.iloc[-1]
    c_close = float(c.close)
    c_middle = float(c.kc_middle)
    c1_middle = float(c1.kc_middle)
    c_ma15 = float(c.ma15)
    c1_ma15 = float(c1.ma15)
    
    if side == 'LONG':
        if c_close <= c_middle:
            return wait('BLOCKED_LONG_PRICE_BELOW_KC_MID')
        if c_middle < c1_middle or c_ma15 < c1_ma15:
            return wait('BLOCKED_LONG_AGAINST_TREND')
    elif side == 'SHORT':
        if c_close >= c_middle:
            return wait('BLOCKED_SHORT_PRICE_ABOVE_KC_MID')
        if c_middle > c1_middle or c_ma15 > c1_ma15:
            return wait('BLOCKED_SHORT_AGAINST_TREND')

    # CK 趨勢嚴格同向要求 (Global CK Alignment Block)
    if side == 'LONG':
        if not (c_middle > c1_middle and float(c.kc_upper) >= float(c1.kc_upper)):
            return wait('BLOCKED_CK_NOT_ALIGNED_LONG')
    elif side == 'SHORT':
        if not (c_middle < c1_middle and float(c.kc_lower) <= float(c1.kc_lower)):
            return wait('BLOCKED_CK_NOT_ALIGNED_SHORT')

    # (已依照妖幣鐵律刪除：通道寬度門檻與支撐位門檻)

    # 兩根同色實體要求移至 rule 判定後，以豁免 IGNITION

    if side == 'LONG':
        problem = long_entry_trend_problem(closed)
        if problem:
            return wait(problem)
    else:
        # 嚴格的「趨勢全線向下共振」防護門檻 (所有空單必須通過)
        kc_mid_falling = float(c.kc_middle) < float(c1.kc_middle)
        ma15_falling = float(c.ma15) < float(c1.ma15)
        close_below_mid = float(c.close) < float(c.kc_middle)
        bearish_aligned = float(c.ma3) < float(c.ma15) and float(c.ma15) <= float(c.kc_middle)
        
        if not (kc_mid_falling and ma15_falling and close_below_mid and bearish_aligned):
            return wait('BLOCKED_SHORT_NOT_ALL_DOWNWARD_RESONANCE')

    # Peak trough cross is completely removed

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
        # TREND_CRAWLING 已永久停用

                
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
        # TREND_CRAWLING 已永久停用

            
    if sign * (c_close - c_open) <= 0:
        return wait('BLOCKED_OPPOSITE_CLOSED_BODY')

    if rule is None:
        return wait('WAIT_NEW_IGNITION_OR_BREAKOUT_TRIGGER')

    # ══════════════════════════════════════════════════════════
    # 終極守門員：嚴禁 TREND_CRAWLING 或任何未授權策略出門
    # 唯一合法入口：IGNITION（外軌起爆）與 TREND_BREAKOUT（兩根破軌確認）
    # ══════════════════════════════════════════════════════════
    if rule not in ('IGNITION', 'TREND_BREAKOUT'):
        return wait(f'BLOCKED_ILLEGAL_RULE_{rule}_ONLY_IGNITION_BREAKOUT_ALLOWED')

    # IGNITION 起爆豁免「必須連續兩根同色」
    if rule != 'IGNITION':
        c1 = closed.iloc[-2]
        def get_body_info(row):
            r_open, r_close, r_high, r_low = float(row.open), float(row.close), float(row.high), float(row.low)
            r_range = r_high - r_low
            r_body = r_close - r_open
            r_ratio = abs(r_body) / r_range if r_range > 1e-9 else 0
            return r_body, r_ratio
            
        c1_body, c1_ratio = get_body_info(c1)
        c_body_val, c_ratio = get_body_info(c)
        
        if side == "LONG":
            if c1_body <= 0 or c_body_val <= 0:
                return wait("BLOCKED_NOT_TWO_GREEN_CANDLES")
        else:
            if c1_body >= 0 or c_body_val >= 0:
                return wait("BLOCKED_NOT_TWO_RED_CANDLES")
                
        if c1_ratio < 0.20 or c_ratio < 0.20:
            return wait("BLOCKED_BODY_RATIO_UNDER_20_PCT")

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

    # 【長影線插針防護 (Pin Bar Rejection Filter)】- 適用於所有入場 (包含 IGNITION)
    candle_len = float(c.high) - float(c.low)
    
    if side == 'SHORT':
        lower_wick = min(float(c.close), float(c.open)) - float(c.low)
        if lower_wick > 0.8 * candle_body or (candle_len > 0 and lower_wick / candle_len > 0.4):
            return wait(f"PIN_BAR_DETECTED (當根下探回升滯跌，嚴禁追空, wick={lower_wick:.5f}, body={candle_body:.5f})")
            
        prev_body = abs(float(c1.close) - float(c1.open))
        prev_lower_wick = min(float(c1.close), float(c1.open)) - float(c1.low)
        if float(c1.low) < float(c1.kc_lower) and prev_lower_wick > 0.8 * prev_body:
            return wait("PREV_PIN_BAR_REJECTION (前根K棒已在下軌外插出長下影線，嚴禁追空)")
    else:
        upper_wick = float(c.high) - max(float(c.close), float(c.open))
        if upper_wick > 0.8 * candle_body or (candle_len > 0 and upper_wick / candle_len > 0.4):
            return wait(f"PIN_BAR_DETECTED (當根衝高回落滯漲，嚴禁追多, wick={upper_wick:.5f}, body={candle_body:.5f})")
            
        prev_body = abs(float(c1.close) - float(c1.open))
        prev_upper_wick = float(c1.high) - max(float(c1.close), float(c1.open))
        if float(c1.high) > float(c1.kc_upper) and prev_upper_wick > 0.8 * prev_body:
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
