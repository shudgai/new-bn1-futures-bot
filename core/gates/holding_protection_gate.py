"""Holding Protection Exit Gate: Protects active positions from premature pullback exits.

Enforces an explicit exit whitelist:
1. EXIT_BY_EXTREME_WATERFALL: An adverse body >= 1.5*ATR that crosses KC middle.
2. EXIT_BY_CIRCUIT_BREAKER_HARD_SL: Hard Stop Loss or market circuit breaker.
3. EXIT_BY_RATCHET_PROFIT_LOCK: net ROE activates at 10%, advances every 1 point,
   and locks the reached step 2 points below its peak threshold.
4. Fractal pullback exits require peak gain >3*ATR, or >2*ATR while
   the established favorable extreme still qualifies as a strong trend.
5. INTRABAR_SPIKE_REBOUND_EXIT closes on a live extreme-KC spike and rebound.
6. Independent buying/selling-pressure exits are rejected. Pressure evidence
   may still corroborate a ratchet profit lock.

All normal breathing / pullbacks (MA touch, upper/lower wick breathing, minor momentum turns)
are strictly REJECTED.
"""
import math
from typing import Tuple, Optional, Dict, Any
import pandas as pd
from core.intelligence.spatial_brain import SpatialBrain
from core.services.exits.net_roe_profit_lock import (
    has_reached_net_roe_floor,
    tiered_net_roe_floor,
)


def _fractal_pullback_exit_authorized(position, frame, side, quote, atr, favorable_move):
    """Allow a fractal pullback only after the approved exceptional extension."""
    try:
        atr, favorable_move = float(atr), float(favorable_move)
        if (frame is None or len(frame) < 2 or atr <= 0
                or not math.isfinite(atr) or not math.isfinite(favorable_move)):
            return False
        gain_atr = favorable_move / atr
        if gain_atr > 3.0:
            return True
        if gain_atr <= 2.0:
            return False
        current, previous = frame.iloc[-1], frame.iloc[-2]
        ma5, ma15 = float(current['ma5']), float(current['ma15'])
        prior_ma5, prior_ma15 = float(previous['ma5']), float(previous['ma15'])
        middle = float(current.get('kc_middle', current.get('kc_basis', 0.0)))
        # Judge trend strength at the established favorable extreme; the
        # pullback quote itself is expected to have turned against MA5.
        price = (float(position.get('entry_price', 0.0)) + favorable_move
                 if side == 'LONG'
                 else float(position.get('entry_price', 0.0)) - favorable_move)
        if side == 'LONG':
            return (ma5 > ma15 and ma5 > prior_ma5 and ma15 >= prior_ma15
                    and price > middle and price >= ma5)
        return (ma5 < ma15 and ma5 < prior_ma5 and ma15 <= prior_ma15
                and price < middle and price <= ma5)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def is_fractal_peak(bars: pd.DataFrame) -> bool:
    """Check if the previous bar forms a 3-bar fractal peak."""
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars.iloc[-3], bars.iloc[-2], bars.iloc[-1]
    h1, h2, h3 = float(b1['high']), float(b2['high']), float(b3['high'])
    return h2 > h1 and h2 >= h3


def is_fractal_valley(bars: pd.DataFrame) -> bool:
    """Check if the previous bar forms a 3-bar fractal valley."""
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars.iloc[-3], bars.iloc[-2], bars.iloc[-1]
    l1, l2, l3 = float(b1['low']), float(b2['low']), float(b3['low'])
    return l2 < l1 and l2 <= l3


def has_confirmed_true_pivot(
    position: Dict[str, Any],
    frame: Optional[pd.DataFrame],
    quote: float,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """檢驗持倉是否已經成型技術面確認的『真峰谷（True Peak/Valley Pivot）』。
    
    規則：開倉後，不要平倉，要到真峰谷才平倉。
    多單（LONG）：
      必須在已收線 K 線中形成頂部三點樞紐（中間 High 最高且兩側較低）或分形頂確認。
    空單（SHORT）：
      必須在已收線 K 線中形成底部三點樞紐（中間 Low 最低且兩側較高）或分形底確認。
    """
    if frame is None or len(frame) < 3:
        return False, None, {}

    side = str((position or {}).get('side', '')).upper()
    if side not in ('LONG', 'SHORT'):
        return False, None, {}

    try:
        last_bar = frame.iloc[-1]
        is_live_closed = bool(last_bar.get('is_closed', False))
        closed = frame if is_live_closed else frame.iloc[:-1]

        # ──【開倉前歷史雜訊過濾】：真峰谷必須在開倉後（或至少由開倉棒推進）形成，絕不能拿開倉前的歷史凹槽平倉！──
        entry_bar_floor_ms = 0.0
        try:
            opened_at = (
                (position or {}).get('open_timestamp')
                or (position or {}).get('entry_timestamp')
                or (position or {}).get('open_bar_id')
                or (position or {}).get('entry_bar_id')
                or ((position or {}).get('entry_snapshot') or {}).get('entry_bar_id')
                or ((position or {}).get('entry_snapshot') or {}).get('timestamp')
            )
            if opened_at is not None:
                opened_at_f = float(opened_at)
                if math.isfinite(opened_at_f) and opened_at_f > 0:
                    opened_ms = opened_at_f * 1000.0 if opened_at_f < 1e11 else opened_at_f
                    timeframe_ms = float(frame.attrs.get('timeframe_ms', 60_000) or 60_000)
                    entry_bar_floor_ms = math.floor(opened_ms / timeframe_ms) * timeframe_ms
        except Exception:
            entry_bar_floor_ms = 0.0

        # ──【第一層：歷史已收線 K 棒成型之真峰谷】──
        if len(closed) >= 3:
            check_len = min(len(closed), 6)
            sub = closed.iloc[-check_len:]

            if side == 'LONG':
                for i in range(len(sub) - 2):
                    b_before = sub.iloc[i]
                    b_pivot = sub.iloc[i + 1]
                    b_confirm = sub.iloc[i + 2]

                    ts_confirm = float(b_confirm.get('timestamp', 0.0))
                    ts_pivot = float(b_pivot.get('timestamp', 0.0))
                    # 歷史雜訊過濾：真峰頂必須在開倉後推進成型，確認棒必須在開倉當根之後，樞紐棒必須至少在開倉當根
                    if entry_bar_floor_ms > 0 and (ts_confirm <= entry_bar_floor_ms or ts_pivot < entry_bar_floor_ms):
                        continue

                    h_before = float(b_before['high'])
                    h_pivot = float(b_pivot['high'])
                    h_confirm = float(b_confirm['high'])

                    if h_pivot > h_before and h_pivot > h_confirm:
                        c_confirm = float(b_confirm['close'])
                        return True, 'TRUE_PEAK_PIVOT_CONFIRMED', {
                            'pivot_high': h_pivot,
                            'confirm_close': c_confirm,
                        }

                if is_fractal_peak(closed):
                    ts_last = float(closed.iloc[-1].get('timestamp', 0.0))
                    if entry_bar_floor_ms == 0 or ts_last > entry_bar_floor_ms:
                        last_c = float(closed.iloc[-1]['close'])
                        prev_mid = (float(closed.iloc[-2]['open']) + float(closed.iloc[-2]['close'])) / 2.0
                        if last_c < prev_mid:
                            return True, 'TRUE_FRACTAL_PEAK_CONFIRMED', {
                                'last_close': last_c,
                                'prev_mid': prev_mid,
                            }

            elif side == 'SHORT':
                for i in range(len(sub) - 2):
                    b_before = sub.iloc[i]
                    b_pivot = sub.iloc[i + 1]
                    b_confirm = sub.iloc[i + 2]

                    ts_confirm = float(b_confirm.get('timestamp', 0.0))
                    ts_pivot = float(b_pivot.get('timestamp', 0.0))
                    # 歷史雜訊過濾：真谷底必須在開倉後推進成型，確認棒必須在開倉當根之後，樞紐棒必須至少在開倉當根
                    if entry_bar_floor_ms > 0 and (ts_confirm <= entry_bar_floor_ms or ts_pivot < entry_bar_floor_ms):
                        continue

                    l_before = float(b_before['low'])
                    l_pivot = float(b_pivot['low'])
                    l_confirm = float(b_confirm['low'])

                    if l_pivot < l_before and l_pivot < l_confirm:
                        c_confirm = float(b_confirm['close'])
                        return True, 'TRUE_VALLEY_PIVOT_CONFIRMED', {
                            'pivot_low': l_pivot,
                            'confirm_close': c_confirm,
                        }

                if is_fractal_valley(closed):
                    ts_last = float(closed.iloc[-1].get('timestamp', 0.0))
                    if entry_bar_floor_ms == 0 or ts_last > entry_bar_floor_ms:
                        last_c = float(closed.iloc[-1]['close'])
                        prev_mid = (float(closed.iloc[-2]['open']) + float(closed.iloc[-2]['close'])) / 2.0
                        if last_c > prev_mid:
                            return True, 'TRUE_FRACTAL_VALLEY_CONFIRMED', {
                                'last_close': last_c,
                                'prev_mid': prev_mid,
                            }

        # ──【第二層：盤中即時真峰谷判定（遇真峰谷馬上平倉，不要等 1m 跑完才平倉！）】──
        if len(frame) >= 3:
            b_before = frame.iloc[-3]
            b_pivot = frame.iloc[-2]
            b_live = frame.iloc[-1]

            ts_pivot = float(b_pivot.get('timestamp', 0.0))
            ts_live = float(b_live.get('timestamp', 0.0))
            # 盤中即時真峰谷：即時棒 ts_live 必須嚴格大於開倉當根（ts_live > entry_bar_floor_ms）
            # 絕不允許在開倉當根拿開倉前的歷史 K 棒當成峰谷在盤中秒平倉！
            if entry_bar_floor_ms > 0 and (ts_live <= entry_bar_floor_ms or ts_pivot < entry_bar_floor_ms):
                return False, None, {}

            quote_f = float(quote)
            live_high = max(float(b_live.get('high', quote_f)), quote_f)
            live_low = min(float(b_live.get('low', quote_f)), quote_f)
            atr_f = float(b_pivot.get('atr', b_live.get('atr', 0.001)))
            if atr_f <= 0:
                atr_f = 0.001

            if side == 'LONG':
                h_before = float(b_before['high'])
                h_pivot = float(b_pivot['high'])
                pivot_mid = (float(b_pivot['open']) + float(b_pivot['close'])) / 2.0

                # 前棒形成最高峰，當前盤中未創新高，且價格已向下反轉確認包夾（跌破實體中點且回落 >= 0.35 ATR）
                if h_pivot > h_before and live_high <= h_pivot:
                    if (quote_f < pivot_mid) and (h_pivot - quote_f >= 0.35 * atr_f):
                        return True, 'TRUE_PEAK_PIVOT_INTRABAR_CONFIRMED', {
                            'pivot_high': h_pivot,
                            'quote': quote_f,
                            'intrabar_confirmed': True,
                        }

                # 盤中分形頂即時確認：跌破前棒實體中點且回落達標
                if is_fractal_peak(frame.iloc[:-1]):
                    if (quote_f < pivot_mid) and (h_pivot - quote_f >= 0.35 * atr_f):
                        return True, 'TRUE_FRACTAL_PEAK_INTRABAR_CONFIRMED', {
                            'quote': quote_f,
                            'pivot_mid': pivot_mid,
                            'intrabar_confirmed': True,
                        }

            elif side == 'SHORT':
                l_before = float(b_before['low'])
                l_pivot = float(b_pivot['low'])
                pivot_mid = (float(b_pivot['open']) + float(b_pivot['close'])) / 2.0

                # 前棒形成最低谷，當前盤中未創新低，且價格已向上反彈確認包夾（突破實體中點且反彈 >= 0.35 ATR）
                if l_pivot < l_before and live_low >= l_pivot:
                    if (quote_f > pivot_mid) and (quote_f - l_pivot >= 0.35 * atr_f):
                        return True, 'TRUE_VALLEY_PIVOT_INTRABAR_CONFIRMED', {
                            'pivot_low': l_pivot,
                            'quote': quote_f,
                            'intrabar_confirmed': True,
                        }

                # 盤中分形底即時確認：突破前棒實體中點且反彈達標
                if is_fractal_valley(frame.iloc[:-1]):
                    if (quote_f > pivot_mid) and (quote_f - l_pivot >= 0.35 * atr_f):
                        return True, 'TRUE_FRACTAL_VALLEY_INTRABAR_CONFIRMED', {
                            'quote': quote_f,
                            'pivot_mid': pivot_mid,
                            'intrabar_confirmed': True,
                        }

        return False, None, {}
    except Exception:
        return False, None, {}


INTRABAR_SPIKE_REBOUND_EXIT = 'EXIT_BY_INTRABAR_SPIKE_REBOUND'
INTRABAR_SPIKE_STATE_KEY = 'intrabar_spike_rebound_state'
CHANNEL_PEAK_EXIT_STATE_KEY = 'channel_peak_profit_exit_state'
# Retained as a legacy reason for rejecting stale pending exits. It is no
# longer generated; net-ROE ladder is the sole automatic profit lock.
CHANNEL_PEAK_PROFIT_EXIT = 'EXIT_CHANNEL_PEAK_PROFIT_LOCK'
CHANNEL_MIDLINE_EXIT = {
    'LONG': 'EXIT_LONG_ON_CONFIRMED_MIDLINE_BREAK',
    'SHORT': 'EXIT_SHORT_ON_CONFIRMED_MIDLINE_BREAK',
}
SHORT_LOWER_BREAK_BODY_ATR = 0.50
SHORT_LOWER_BREAK_REBOUND_RATIO = 0.35


def short_lower_break_exit_confirmation(frame, quote, position=None):
    """Describe whether a strong short-side lower-rail break may be exited."""
    inactive = {
        'active': False,
        'confirmed': False,
        'confirmation': None,
    }
    if frame is None or len(frame) < 2:
        return inactive
    try:
        quote = float(quote)
        if not math.isfinite(quote) or quote <= 0:
            return inactive
        trigger_floor_ms = None
        if isinstance(position, dict):
            opened_at = (
                position.get('open_timestamp')
                or position.get('entry_timestamp')
                or position.get('open_bar_id')
                or position.get('entry_bar_id')
                or position.get('candidate_bar_id')
            )
            if opened_at is not None:
                opened_at = float(opened_at)
                if math.isfinite(opened_at) and opened_at > 0:
                    opened_ms = opened_at * 1000.0 if opened_at < 1e11 else opened_at
                    timeframe_ms = float(
                        frame.attrs.get('timeframe_ms', 60_000) or 60_000
                    )
                    if math.isfinite(timeframe_ms) and timeframe_ms > 0:
                        trigger_floor_ms = (
                            math.floor(opened_ms / timeframe_ms) * timeframe_ms
                        )

        trigger_index = None
        trigger_open = None
        trigger_atr = None
        for index in range(len(frame) - 1, 0, -1):
            candle = frame.iloc[index]
            previous = frame.iloc[index - 1]
            opening = float(candle['open'])
            close = float(candle['close'])
            low = float(candle['low'])
            lower = float(candle['kc_lower'])
            atr = float(previous.get('atr', candle.get('atr', 0.0)))
            timestamp = float(candle.get('timestamp', 0.0))
            if trigger_floor_ms is not None and timestamp < trigger_floor_ms:
                continue
            is_current = index == len(frame) - 1
            effective_low = min(low, quote) if is_current else low
            candle_closed = bool(candle.get('is_closed', False))
            completed_body_break = (
                candle_closed and close < opening
                and opening - close >= SHORT_LOWER_BREAK_BODY_ATR * atr
                and close < lower
            )
            live_body_break = (
                is_current and not candle_closed
                and opening - effective_low >= SHORT_LOWER_BREAK_BODY_ATR * atr
                and effective_low < lower
            )
            if (all(math.isfinite(value) and value > 0 for value in
                    (opening, close, effective_low, lower, atr))
                    and (completed_body_break or live_body_break)):
                trigger_index = index
                trigger_open = opening
                trigger_atr = atr
                break

        if trigger_index is None:
            return inactive

        trough = quote
        bullish_reclaim = None
        for index in range(trigger_index, len(frame)):
            candle = frame.iloc[index]
            low = float(candle['low'])
            trough = min(trough, low)
            if bool(candle.get('is_closed', False)):
                opening = float(candle['open'])
                close = float(candle['close'])
                lower = float(candle['kc_lower'])
                if close > opening and close > lower:
                    bullish_reclaim = {
                        'bar_id': candle.get('timestamp'),
                        'open': opening,
                        'close': close,
                        'kc_lower': lower,
                    }

        excursion = trigger_open - trough
        rebound = quote - trough
        rebound_ratio = rebound / excursion if excursion > 0 else 0.0
        rebound_confirmed = (
            rebound_ratio > SHORT_LOWER_BREAK_REBOUND_RATIO
            or math.isclose(
                rebound_ratio, SHORT_LOWER_BREAK_REBOUND_RATIO,
                rel_tol=1e-12, abs_tol=0.0,
            )
        )
        confirmation = (
            'BULLISH_CLOSE_RECLAIMED_KC_LOWER'
            if bullish_reclaim is not None
            else 'INTRABAR_REBOUND_35_PERCENT'
            if rebound_confirmed
            else None
        )
        return {
            'active': True,
            'confirmed': confirmation is not None,
            'confirmation': confirmation,
            'trigger_bar_id': frame.iloc[trigger_index].get('timestamp'),
            'trigger_open': trigger_open,
            'trigger_atr': trigger_atr,
            'trough': trough,
            'quote': quote,
            'excursion': excursion,
            'rebound': rebound,
            'rebound_ratio': rebound_ratio,
            'bullish_reclaim': bullish_reclaim,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return inactive


def channel_swing_midline_exit_evidence(frame, side):
    """Require an adverse completed candle body beyond the latest KC midline."""
    if side not in ('LONG', 'SHORT'):
        return None
    try:
        from core.services.candle_data import closed_entry_candles
        closed = closed_entry_candles(frame)
        if closed.empty:
            return None
        current = closed.iloc[-1]
        opening = float(current['open'])
        close = float(current['close'])
        middle = float(current.get(
            'kc_middle', current.get('kc_basis', float('nan')),
        ))
        if not all(math.isfinite(value) and value > 0
                   for value in (opening, close, middle)):
            return None
        if side == 'LONG':
            valid = close < middle and close < opening
        else:
            valid = close > middle and close > opening
        if not valid:
            return None
        return {
            'side': side,
            'crossed_level': 'kc_middle',
            'open': opening,
            'close': close,
            'level': middle,
            'bar_id': current.get('timestamp'),
            'closed_reversal_confirmation': True,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


HOLDING_THROUGH_CHOP_REJECT = 'REJECT_EXIT: HOLDING_THROUGH_CHOP'
_ENTRY_ONLY_CHOP_REASONS = frozenset((
    'BLOCKED_BY_AI_CHOP_REGIME',
    'BLOCKED_MA5_MA15_GAP_TOO_NARROW',
    'BLOCKED_KC_SQUEEZE',
))
_CHOP_EXTREME_EXIT_REASONS = frozenset((
    'EXIT_BY_VERIFIED_FRACTAL_PEAK',
    'EXIT_BY_VERIFIED_FRACTAL_VALLEY',
    'EXIT_LONG_ON_FRACTAL_PEAK',
    'EXIT_SHORT_ON_FRACTAL_VALLEY',
    'EXIT_LONG_ON_TOP_WATERFALL_DUMP',
    'EXIT_SHORT_ON_CONFIRMED_TROUGH',
    'EXIT_SHORT_ON_EXHAUSTION_REVERSAL',
    'EXIT_LONG_ON_EXHAUSTION_REVERSAL',
    INTRABAR_SPIKE_REBOUND_EXIT,
))
_CONFIRMED_STRUCTURE_EXIT = 'EXIT_CONFIRMED_3BAR_STRUCTURE_BREAK'


def _is_choppy_or_squeezed_holding(position, frame):
    """Inspect completed market structure only; this is an exit immunity gate."""
    if frame is None or frame.empty:
        return False, {}
    side = str((position or {}).get('side', '')).upper()
    symbol = str((position or {}).get('symbol') or '')
    evidence = {}
    try:
        from core.services.candle_data import closed_entry_candles
        closed = closed_entry_candles(frame)
        if closed.empty:
            return False, evidence
        current = closed.iloc[-1]
        close = float(current['close'])
        middle = float(current.get('kc_middle', current.get('kc_basis', float('nan'))))
        upper, lower = float(current['kc_upper']), float(current['kc_lower'])
        width = upper - lower
        bandwidth = width / middle if middle > 0 else float('nan')
        regime = 'UNKNOWN'
        if side in ('LONG', 'SHORT'):
            from core.services.exits.profit_protection_service import assess_market_regime
            regime = assess_market_regime(frame, side, (position or {}).get('open_timestamp'))
        ai_choppy = False
        try:
            from core.gates.pipeline import pipeline
            provider = getattr(pipeline, 'market_regime_provider', None)
            if callable(provider):
                ai_choppy = str(provider(symbol)).upper() == 'CHOPPY'
        except Exception:
            ai_choppy = False
        ma_gap_narrow = False
        if {'ma5', 'ma15'}.issubset(closed.columns) and close > 0:
            from core.gates.pipeline import MA_GAP_CHOP_MIN_PCT
            ma_gap = abs(float(current['ma5']) - float(current['ma15'])) / close
            ma_gap_narrow = math.isfinite(ma_gap) and ma_gap < MA_GAP_CHOP_MIN_PCT
        from core.gates.pipeline import MIN_KC_BANDWIDTH_PCT
        kc_squeezed = math.isfinite(bandwidth) and bandwidth < MIN_KC_BANDWIDTH_PCT
        evidence = {
            'market_regime': regime,
            'ai_choppy': ai_choppy,
            'ma_gap_narrow': ma_gap_narrow,
            'kc_squeezed': kc_squeezed,
            'closed_close': close,
            'kc_middle': middle,
        }
        return regime == 'CHOPPY' or ai_choppy or ma_gap_narrow or kc_squeezed, evidence
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False, evidence


def holding_chop_exit_problem(position, frame, candidate_reason, details=None):
    """Veto non-stop, non-extreme exits during chop/squeeze unless KC midline broke."""
    reason = str(candidate_reason or '').replace('Channel Swing ', '').strip()
    if reason in _ENTRY_ONLY_CHOP_REASONS:
        return HOLDING_THROUGH_CHOP_REJECT, {'entry_gate_reason': reason}
    if any(marker in reason.upper() for marker in (
            'HARD_STOP', 'CIRCUIT_BREAKER', 'HARD_SL', 'ACCOUNT_HARD_STOP',
            'EMERGENCY_STOP', 'EXIT_BY_CIRCUIT_BREAKER_HARD_SL')):
        return None, {}
    choppy, regime_details = _is_choppy_or_squeezed_holding(position, frame)
    if not choppy:
        return None, regime_details
    side = str((position or {}).get('side', '')).upper()
    if reason in _CHOP_EXTREME_EXIT_REASONS:
        return None, regime_details
    if reason in (CHANNEL_MIDLINE_EXIT.get(side), _CONFIRMED_STRUCTURE_EXIT):
        if channel_swing_midline_exit_evidence(frame, side) is not None:
            return None, regime_details
    if details is not None:
        regime_details['candidate_details'] = details
    return HOLDING_THROUGH_CHOP_REJECT, regime_details


def _last_closed_close_at_or_above_kc_middle(frame):
    """Return whether the latest completed close still holds above KC middle."""
    try:
        from core.services.candle_data import closed_entry_candles
        closed = closed_entry_candles(frame)
        if closed.empty:
            return None
        current = closed.iloc[-1]
        close = float(current['close'])
        middle = float(current.get(
            'kc_middle', current.get('kc_basis', float('nan')),
        ))
        if not all(math.isfinite(value) and value > 0 for value in (close, middle)):
            return None
        return close >= middle
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def channel_peak_profit_exit_evidence(position, frame, quote):
    """Retired legacy ATR/giveback lock; net-ROE ladder is the sole profit lock."""
    return None, False


def intrabar_spike_rebound_evidence(position, frame, side, quote):
    """Track post-entry live-bar extremes and verify an extreme-KC rebound."""
    if (not position or frame is None or frame.empty or side not in ('LONG', 'SHORT')
            or _current_bar_is_closed(frame)):
        return None
    try:
        quote = float(quote)
        current = frame.iloc[-1]
        opening = float(current['open'])
        raw_high = float(current['high'])
        raw_low = float(current['low'])
        upper = float(current['kc_upper'])
        lower = float(current['kc_lower'])
        bar_id = float(current['timestamp'])
        entry_price = float(position.get('entry_price') or 0.0)
        opened_at = float(
            position.get('open_timestamp')
            or position.get('entry_timestamp')
            or position.get('open_bar_id')
            or 0.0
        )
        if opened_at > 0 and opened_at < 1e11:
            opened_at *= 1000.0
        values = (quote, opening, raw_high, raw_low, upper, lower, bar_id, entry_price)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or raw_low > min(opening, raw_high)
                or raw_high < max(opening, raw_low)
                or lower >= upper):
            return None

        identity = [side, opened_at, entry_price]
        saved = position.get(INTRABAR_SPIKE_STATE_KEY)
        same_bar_state = (
            isinstance(saved, dict)
            and saved.get('identity') == identity
            and float(saved.get('bar_id') or 0.0) == bar_id
        )
        candle_started_after_entry = opened_at > 0 and opened_at < bar_id
        if same_bar_state:
            tracked_high = float(saved['high'])
            tracked_low = float(saved['low'])
        elif candle_started_after_entry:
            tracked_high = raw_high
            tracked_low = raw_low
        else:
            # OHLC may include movement from before a same-candle entry. Start
            # from an observed post-entry quote instead of reconstructing it.
            tracked_high = quote
            tracked_low = quote

        if candle_started_after_entry:
            tracked_high = max(tracked_high, raw_high)
            tracked_low = min(tracked_low, raw_low)
        tracked_high = max(tracked_high, quote)
        tracked_low = min(tracked_low, quote)
        next_state = {
            'identity': identity,
            'bar_id': bar_id,
            'high': tracked_high,
            'low': tracked_low,
        }
        if next_state != saved:
            position[INTRABAR_SPIKE_STATE_KEY] = next_state

        candle_range = tracked_high - tracked_low
        if candle_range <= 0:
            return None
        if side == 'SHORT':
            if tracked_low >= lower:
                return None
            rebound_ratio = (quote - tracked_low) / candle_range
            ratio_reached = (
                rebound_ratio >= 0.35
                or math.isclose(rebound_ratio, 0.35, rel_tol=1e-12)
            )
            if not ratio_reached:
                return None
            return {
                'side': side, 'trigger': 'SPIKE_REBOUND_RATIO', 'quote': quote,
                'curr_low': tracked_low, 'curr_high': tracked_high,
                'kc_lower': lower, 'rebound_ratio': rebound_ratio,
                'bar_id': bar_id,
                'intrabar_confirmation': True,
            }

        if tracked_high <= upper:
            return None
        retracement_ratio = (tracked_high - quote) / candle_range
        if (retracement_ratio < 0.35
                or math.isclose(retracement_ratio, 0.35, rel_tol=1e-12)):
            return None
        return {
            'side': side, 'trigger': 'SPIKE_RETRACEMENT_RATIO',
            'quote': quote, 'curr_high': tracked_high, 'curr_low': tracked_low,
            'kc_upper': upper, 'retracement_ratio': retracement_ratio,
            'bar_id': bar_id, 'intrabar_confirmation': True,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _current_bar_is_closed(frame):
    if frame is None or frame.empty:
        return False
    value = frame.iloc[-1].get('is_closed', False)
    return value is True or (type(value).__name__ == 'bool_' and bool(value))


def _closed_bar_crossed_kc_middle(frame, side):
    """Require a confirmed close across KC mid before a soft profit exit."""
    if frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT'):
        return False
    if not _current_bar_is_closed(frame):
        return False
    try:
        previous, current = frame.iloc[-2], frame.iloc[-1]
        previous_close = float(previous['close'])
        previous_middle = float(previous.get(
            'kc_middle', previous.get('kc_basis', float('nan')),
        ))
        close = float(current['close'])
        middle = float(current.get(
            'kc_middle', current.get('kc_basis', float('nan')),
        ))
        values = (previous_close, previous_middle, close, middle)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return False
        if side == 'LONG':
            return previous_close >= previous_middle and close < middle
        return previous_close <= previous_middle and close > middle
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def confirmed_midline_reversal_evidence(frame, side, min_body_atr=0.5):
    """A soft strategy exit requires a directional completed body beyond KC middle."""
    if side not in ('LONG', 'SHORT'):
        return None
    try:
        from core.services.candle_data import closed_entry_candles
        closed = closed_entry_candles(frame)
        if closed.empty:
            return None
        current = closed.iloc[-1]
        opening, close = float(current['open']), float(current['close'])
        middle = float(current.get(
            'kc_middle', current.get('kc_basis', float('nan')),
        ))
        if not all(math.isfinite(value) and value > 0
                   for value in (opening, close, middle)):
            return None

        beyond_middle = close < middle if side == 'LONG' else close > middle
        adverse_body = opening > close if side == 'LONG' else close > opening
        if not beyond_middle or not adverse_body:
            return None
        atr = float(current.get('atr', float('nan')))
        body = opening - close if side == 'LONG' else close - opening
        return {
            'side': side, 'crossed_level': 'kc_middle',
            'close': close, 'level': middle, 'body': body,
            'atr': atr, 'body_atr': body / atr if atr > 0 else None,
            'bar_id': current.get('timestamp'),
            'closed_reversal_confirmation': True,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def confirmed_short_bottom_evidence(frame, volume_multiple=1.5):
    """Confirm a short-covering trough by volume climax or two defended lows."""
    try:
        from core.services.candle_data import closed_entry_candles
        closed = closed_entry_candles(frame)
        if len(closed) < 3:
            return None
        current = closed.iloc[-1]
        opening, close = float(current['open']), float(current['close'])
        high, low = float(current['high']), float(current['low'])
        ma5 = float(current['ma5'])
        atr = float(closed.iloc[-2]['atr'])
        candle_range = high - low
        if (not all(math.isfinite(value) and value > 0 for value in
                    (opening, close, high, low, ma5, atr))
                or candle_range <= 0 or atr <= 0
                or high < max(opening, close) or low > min(opening, close)):
            return None

        lower_wick = min(opening, close) - low
        wick_ratio = lower_wick / candle_range
        if wick_ratio >= 0.45 and 'volume' in closed:
            prior_volumes = pd.to_numeric(
                closed['volume'].iloc[-21:-1], errors='coerce',
            ).dropna()
            volume = float(current['volume'])
            baseline = float(prior_volumes.mean()) if len(prior_volumes) >= 5 else 0.0
            volume_ratio = volume / baseline if baseline > 0 else 0.0
            if math.isfinite(volume_ratio) and volume_ratio >= volume_multiple:
                return {
                    'kind': 'VOLUME_CLIMAX_LOWER_WICK', 'lower_wick': lower_wick,
                    'wick_ratio': wick_ratio, 'volume_ratio': volume_ratio,
                    'volume_multiple': volume_multiple,
                    'close': close, 'ma5': ma5, 'bar_id': current.get('timestamp'),
                }

        if len(closed) >= 4:
            lows = [float(value) for value in closed['low'].iloc[-3:]]
            if (all(math.isfinite(value) and value > 0 for value in lows)
                    and lows[1] >= lows[0] and lows[2] >= lows[1]
                    and close > ma5):
                return {
                    'kind': 'TWO_DEFENDED_LOWS_MA5_RECLAIM',
                    'lows': lows, 'close': close, 'ma5': ma5,
                    'bar_id': current.get('timestamp'),
                }
        return None
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _is_profit_lock_reason(reason: str) -> bool:
    r = str(reason or '').upper()
    return (
        r == 'EXIT_BY_RATCHET_PROFIT_LOCK'
        or r.startswith('PROFIT_LOCK')
        or r.startswith('NET_ROE_THREE_TIER_GIVEBACK')
        or 'RATCHET' in r
        or 'BREAKEVEN' in r
        or 'PROFIT_FLOOR' in r
        or 'PROFIT_LOCK' in r
    )


def _verified_profit_lock_details(position, quote):
    """Return evidence only when an armed profit floor is actually touched."""
    details = _ratchet_lock_details(position, quote)
    if details is not None:
        return details

    try:
        side = str((position or {}).get('side', '')).upper()
        entry = float((position or {}).get('entry_price') or 0.0)
        price = float(quote)
        if side not in ('LONG', 'SHORT') or entry <= 0 or price <= 0:
            return None
        lock_state = (position or {}).get('channel_profit_protection') or {}
        is_armed = (
            (position or {}).get('breakeven_locked') is True
            or (position or {}).get('is_breakeven_moved') is True
            or (position or {}).get('breakeven_price') is not None
            or (isinstance(lock_state, dict) and lock_state.get('armed') is True)
        )
        floor = float(
            (position or {}).get('breakeven_price')
            or (position or {}).get('active_stop_price')
            or (lock_state.get('breakeven_price') if isinstance(lock_state, dict) else 0.0)
            or 0.0
        )
        touched = (
            price <= floor if side == 'LONG' else price >= floor
        )
        if is_armed and floor > 0 and touched:
            return {'lock_type': 'ARMED_BREAKEVEN_OR_PROFIT_FLOOR', 'lock_price': floor}
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return None



def is_same_bar_as_entry(position: Dict[str, Any], frame: Optional[pd.DataFrame]) -> bool:
    """檢查當前狀態是否處於開倉進場的同一根 K 棒（Same-Bar）。
    
    鐵律：開倉後不要當根平倉，除非剛好是真峰谷，否則除了鎖利外都不平倉。
    """
    if frame is None or len(frame) == 0 or not isinstance(position, dict):
        return False
    try:
        opened_at = (
            position.get('open_timestamp')
            or position.get('entry_timestamp')
            or position.get('open_bar_id')
            or position.get('entry_bar_id')
            or position.get('candidate_bar_id')
            or (position.get('entry_snapshot') or {}).get('entry_bar_id')
            or (position.get('entry_snapshot') or {}).get('timestamp')
        )
        if opened_at is None:
            return False

        opened_at_f = float(opened_at)
        if not math.isfinite(opened_at_f) or opened_at_f <= 0:
            return False

        opened_ms = opened_at_f * 1000.0 if opened_at_f < 1e11 else opened_at_f

        live_candle = frame.iloc[-1]
        live_ts = float(live_candle.get('timestamp', 0.0))
        if live_ts <= 0:
            return False
        live_bar_ms = live_ts * 1000.0 if live_ts < 1e11 else live_ts

        timeframe_ms = float(frame.attrs.get('timeframe_ms', 60_000) or 60_000)
        if not math.isfinite(timeframe_ms) or timeframe_ms <= 0:
            timeframe_ms = 60_000.0

        opened_bar_ms = math.floor(opened_ms / timeframe_ms) * timeframe_ms

        if opened_bar_ms == live_bar_ms or opened_ms >= live_bar_ms:
            return True
        return False
    except Exception:
        return False


def _body_breaks_kc_middle(frame, side, price=None):
    """A pullback is protected while its real body stays on the held side of KC mid."""
    if frame is None or frame.empty or side not in ('LONG', 'SHORT'):
        return False
    try:
        current = frame.iloc[-1]
        opening = float(current['open'])
        end = float(current['close']) if price is None else float(price)
        middle = float(current.get('kc_middle', current.get('kc_basis', 0.0)))
        if not all(math.isfinite(value) and value > 0
                   for value in (opening, end, middle)):
            return False
        # A long's body must reach below mid; a short's body must reach above.
        return min(opening, end) < middle if side == 'LONG' else max(opening, end) > middle
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def top_engulfing_flip_evidence(frame):
    """Validate a closed, extended bearish engulfing candle for long-to-short flip."""
    if frame is None or len(frame) < 2 or not _current_bar_is_closed(frame):
        return None
    try:
        current, previous = frame.iloc[-1], frame.iloc[-2]
        opening, close, ma5, ma15 = (
            float(current[key]) for key in ('open', 'close', 'ma5', 'ma15')
        )
        atr = float(previous['atr'])
        previous_open = float(previous['open'])
        previous_close = float(previous['close'])
        previous_low = float(previous['low'])
        values = (opening, close, ma5, ma15, atr, previous_open,
                  previous_close, previous_low)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or atr <= 0 or previous_close <= previous_open):
            return None
        body = opening - close
        if (close - ma15 <= 1.2 * atr or body < 0.4 * atr
                or close >= ma5 or opening < previous_close
                or close >= previous_open):
            return None
        from core.services.strategies.outer_strategy import kc_bearish_midline_turn
        flip_short_allowed = kc_bearish_midline_turn(frame, close)
        return {
            'open': opening,
            'close': close,
            'ma5': ma5,
            'ma15': ma15,
            'previous_close': previous_close,
            'previous_low': previous_low,
            'atr': atr,
            'body_atr': body / atr,
            'extension_atr': (close - ma15) / atr,
            'flip_short_allowed': flip_short_allowed,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def exhaustion_reversal_evidence(frame, side):
    """Detect a closed reversal body after recent weak candles or rejection wicks."""
    if (frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT')
            or not _current_bar_is_closed(frame)):
        return None
    try:
        current = frame.iloc[-1]
        previous = frame.iloc[-2]
        opening, close, high, low, ma5, atr = (
            tuple(float(current[key]) for key in ('open', 'close', 'high', 'low', 'ma5'))
            + (float(previous['atr']),)
        )
        current_values = (opening, close, high, low, ma5, atr)
        if (not all(math.isfinite(value) and value > 0 for value in current_values)
                or low > min(opening, close) or high < max(opening, close)
                or atr <= 0):
            return None

        precursors = []
        for offset in (2, 3):
            if len(frame) < offset:
                continue
            candle = frame.iloc[-offset]
            if 'is_closed' in frame.columns:
                value = candle.get('is_closed', False)
                if not (value is True or (
                    type(value).__name__ == 'bool_' and bool(value)
                )):
                    continue
            candle_open, candle_close, candle_high, candle_low = (
                float(candle[key]) for key in ('open', 'close', 'high', 'low')
            )
            candle_atr = float(candle.get('atr', atr))
            values = (candle_open, candle_close, candle_high, candle_low, candle_atr)
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or candle_low > min(candle_open, candle_close)
                    or candle_high < max(candle_open, candle_close)
                    or candle_atr <= 0):
                continue
            body = abs(candle_close - candle_open)
            rejection_wick = (
                min(candle_open, candle_close) - candle_low
                if side == 'SHORT'
                else candle_high - max(candle_open, candle_close)
            )
            if body < 0.25 * candle_atr or rejection_wick >= 0.35 * candle_atr:
                precursors.append({
                    'bar_id': candle.get('timestamp'),
                    'high': candle_high,
                    'low': candle_low,
                    'body': body,
                    'rejection_wick': rejection_wick,
                    'atr': candle_atr,
                })

        if not precursors:
            return None
        body = abs(close - opening)
        if side == 'SHORT':
            reversal = close > opening and close > ma5 and (
                body >= 0.3 * atr or any(close > candle['high'] for candle in precursors)
            )
        else:
            reversal = close < opening and close < ma5 and (
                body >= 0.3 * atr or any(close < candle['low'] for candle in precursors)
            )
        if not reversal:
            return None
        return {
            'atr': atr, 'open': opening, 'close': close, 'ma5': ma5,
            'body': body, 'precursor_bars': [item['bar_id'] for item in precursors],
            'precursor_evidence': precursors,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _short_reversal_body_evidence(frame, quote, atr):
    if frame is None or frame.empty or not math.isfinite(atr) or atr <= 0:
        return None
    try:
        live = frame.iloc[-1]
        opening = float(live['open'])
        effective = float(live['close'])
        high = float(live['high'])
        low = float(live['low'])
        body = effective - opening
        candle_range = high - low
        upper_wick = high - max(opening, effective)
        ma5 = float(live['ma5'])
        previous_mid = None
        if len(frame) > 1:
            previous = frame.iloc[-2]
            previous_mid = (
                float(previous['open']) + float(previous['close'])
            ) / 2.0
        middle = float(live.get('kc_middle', live.get('kc_basis', 0.0)))
        values = (opening, effective, high, low, body, candle_range,
                  upper_wick, ma5, middle)
        if (not all(math.isfinite(value) for value in values)
                or min(opening, effective, high, low, ma5) <= 0
                or candle_range <= 0 or high < max(opening, effective)
                or low > min(opening, effective)
                or not _current_bar_is_closed(frame)):
            return None
        body_ratio = body / candle_range
        reversal = closed_peak_valley_reversal_evidence(frame, 'SHORT', atr)
        if (reversal is None or body <= 0 or upper_wick >= 0.4 * atr
                or effective <= ma5):
            return None
        return {
            'effective_price': effective, 'prev_mid': previous_mid,
            'ma5': ma5, 'kc_middle': middle, 'atr': atr,
            'body': body, 'body_ratio': body_ratio,
            'upper_wick': upper_wick, **reversal,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _long_reversal_body_evidence(frame, quote, atr):
    if frame is None or frame.empty or not math.isfinite(atr) or atr <= 0:
        return None
    try:
        live = frame.iloc[-1]
        opening = float(live['open'])
        effective = float(live['close'])
        high = float(live['high'])
        low = float(live['low'])
        body = opening - effective
        candle_range = high - low
        lower_wick = min(opening, effective) - low
        ma5 = float(live['ma5'])
        values = (opening, effective, high, low, body, candle_range, lower_wick, ma5)
        if (not all(math.isfinite(value) for value in values)
                or min(opening, effective, high, low, ma5) <= 0
                or candle_range <= 0 or high < max(opening, effective)
                or low > min(opening, effective)
                or not _current_bar_is_closed(frame)):
            return None
        reversal = closed_peak_valley_reversal_evidence(frame, 'LONG', atr)
        if reversal is None or lower_wick >= 0.4 * atr or effective >= ma5:
            return None
        return {
            'effective_price': effective, 'ma5': ma5, 'atr': atr,
            'body': body, 'body_ratio': body / candle_range,
            'lower_wick': lower_wick, **reversal,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def closed_peak_valley_reversal_evidence(frame, side, atr):
    """Require a meaningful, structurally confirmed closed-bar reversal."""
    if (frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT')
            or not math.isfinite(atr) or atr <= 0 or not _current_bar_is_closed(frame)):
        return None
    try:
        current, previous = frame.iloc[-1], frame.iloc[-2]
        opening, close, ma5 = (float(current[key]) for key in ('open', 'close', 'ma5'))
        previous_mid = (float(previous['open']) + float(previous['close'])) / 2.0
        high, low = float(current['high']), float(current['low'])
        values = (opening, close, ma5, previous_mid, high, low)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or high < max(opening, close) or low > min(opening, close)):
            return None

        body = abs(close - opening)
        if side == 'LONG':
            reversed_body = close < opening
            structural_break = close < ma5 and close < previous_mid
            main_rail_broken = min(opening, close) < float(
                current.get('kc_middle', current.get('kc_basis', float('nan')))
            )
            confirmed_pivot = False
            if len(frame) >= 3:
                before = frame.iloc[-3]
                confirmed_pivot = (
                    float(previous['high']) > float(before['high'])
                    and float(previous['high']) > high
                )
        else:
            reversed_body = close > opening
            structural_break = close > ma5 and close > previous_mid
            main_rail_broken = max(opening, close) > float(
                current.get('kc_middle', current.get('kc_basis', float('nan')))
            )
            confirmed_pivot = False
            if len(frame) >= 3:
                before = frame.iloc[-3]
                confirmed_pivot = (
                    float(previous['low']) < float(before['low'])
                    and float(previous['low']) < low
                )
        # Dojis and weak opposite bodies remain observation signals.  Exit on
        # this bar only when the body breaks both structures. A 0.35-0.75 ATR
        # reversal also needs volume expansion; this filters ordinary retests.
        if not (reversed_body and body >= 0.35 * atr and structural_break
                and (main_rail_broken or confirmed_pivot)):
            return None
        volume_ratio = None
        if 'volume' in frame.columns and len(frame) >= 7:
            prior_volumes = [float(value) for value in frame['volume'].iloc[-6:-1]]
            current_volume = float(current['volume'])
            baseline = sum(prior_volumes) / len(prior_volumes)
            if (all(math.isfinite(value) and value >= 0 for value in prior_volumes)
                    and math.isfinite(current_volume) and baseline > 0):
                volume_ratio = current_volume / baseline
        if body < 0.75 * atr and (volume_ratio is None or volume_ratio < 1.2):
            return None
        return {
            'open': opening, 'close': close, 'ma5': ma5,
            'previous_midpoint': previous_mid, 'atr': atr,
            'body': body, 'is_doji': body <= 0.1 * atr,
            'volume_ratio': volume_ratio,
            'main_rail_body_breached': bool(main_rail_broken),
            'confirmed_fractal_pivot': bool(confirmed_pivot),
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def intrabar_adverse_pressure_evidence(frame, side, quote, atr):
    """Separate a forceful, structure-breaking reversal from an ordinary pullback."""
    if (frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT')
            or not all(math.isfinite(float(value)) and float(value) > 0
                       for value in (quote, atr))):
        return None
    try:
        current, previous = frame.iloc[-1], frame.iloc[-2]
        opening = float(current['open'])
        ma5 = float(current['ma5'])
        previous_mid = (float(previous['open']) + float(previous['close'])) / 2.0
        price = float(quote)
        body = opening - price if side == 'LONG' else price - opening
        if not _body_breaks_kc_middle(frame, side, price):
            return None
        if side == 'LONG':
            structure_broken = price < ma5 and price < previous_mid
        else:
            structure_broken = price > ma5 and price > previous_mid
        if not math.isfinite(body) or body < 0.35 * atr or not structure_broken:
            return None

        volume_ratio = None
        if 'volume' in frame.columns and len(frame) >= 7:
            prior_volumes = [float(value) for value in frame['volume'].iloc[-6:-1]]
            current_volume = float(current['volume'])
            baseline = sum(prior_volumes) / len(prior_volumes)
            if (all(math.isfinite(value) and value >= 0 for value in prior_volumes)
                    and math.isfinite(current_volume) and baseline > 0):
                volume_ratio = current_volume / baseline
        # A forming candle must show a very large body before it can close
        # immediately. Smaller pressure needs a completed bar plus expansion.
        candle_closed = _current_bar_is_closed(frame)
        if body < 0.75 * atr and not (
                candle_closed and volume_ratio is not None and volume_ratio >= 1.2):
            return None
        return {
            'side': side, 'open': opening, 'quote': price, 'atr': float(atr),
            'adverse_body': body, 'adverse_body_atr': body / float(atr),
            'ma5': ma5, 'previous_midpoint': previous_mid,
            'volume_ratio': volume_ratio,
            'pressure_confirmation': 'VOLUME_EXPANSION' if volume_ratio is not None and volume_ratio >= 1.2 else 'LARGE_BODY',
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def intrabar_peak_valley_reversal_evidence(position, frame, side, quote, atr,
                                           pivot_price, favorable_move):
    """Authorize prompt exits only after a sizeable move rejects a real extreme."""
    if (side not in ('LONG', 'SHORT') or frame is None or len(frame) < 2
            or not all(math.isfinite(value) and value > 0
                       for value in (float(quote), float(atr), float(pivot_price), float(favorable_move)))):
        return None
    entry = float((position or {}).get('entry_price') or 0.0)
    if entry <= 0:
        return None
    excursion = (pivot_price - entry) if side == 'LONG' else (entry - pivot_price)
    if not _fractal_pullback_exit_authorized(
            position, frame, side, quote, atr, favorable_move):
        return None
    retracement = (pivot_price - quote) if side == 'LONG' else (quote - pivot_price)
    if (excursion < 0.5 * atr or favorable_move < 0.5 * atr
            or retracement < max(0.35 * atr, 0.2 * excursion)):
        return None
    current, previous = frame.iloc[-1], frame.iloc[-2]
    opening = float(current.get('open') or 0.0)
    live_body = opening - float(quote) if side == 'LONG' else float(quote) - opening
    if not math.isfinite(live_body) or live_body < 0.35 * atr:
        return None
    ma5 = float(current.get('ma5') or 0.0)
    previous_mid = (
        float(previous.get('open') or 0.0) + float(previous.get('close') or 0.0)
    ) / 2.0
    if (not math.isfinite(ma5) or not math.isfinite(previous_mid)
            or ma5 <= 0 or previous_mid <= 0):
        return None
    structure_broken = (
        quote < ma5 and quote < previous_mid
        if side == 'LONG'
        else quote > ma5 and quote > previous_mid
    )
    if not structure_broken:
        return None
    current_closed = _current_bar_is_closed(frame)
    volume_ratio = None
    if 'volume' in frame.columns and len(frame) >= 7:
        try:
            prior_volumes = [float(value) for value in frame['volume'].iloc[-6:-1]]
            current_volume = float(current['volume'])
            baseline = sum(prior_volumes) / len(prior_volumes)
            if (all(math.isfinite(value) and value >= 0 for value in prior_volumes)
                    and math.isfinite(current_volume) and baseline > 0):
                volume_ratio = current_volume / baseline
        except (TypeError, ValueError, OverflowError):
            volume_ratio = None
    if live_body < 0.75 * atr and not (
            current_closed and volume_ratio is not None and volume_ratio >= 1.2):
        return None
    return {
        'side': side, 'quote': quote, 'atr': atr,
        'pivot_price': pivot_price, 'favorable_move': favorable_move,
        'reversal_body': live_body,
        'ma5': ma5, 'previous_midpoint': previous_mid,
        'volume_ratio': volume_ratio,
        'excursion_atr': excursion / atr, 'retrace_atr': retracement / atr,
    }


def _net_roe_pct(position, price, side):
    try:
        entry = float(position.get('entry_price') or 0.)
        qty = abs(float(position.get('qty', position.get('quantity', 0.)) or 0.))
        margin = float(position.get('margin') or 0.)
        if margin <= 0:
            leverage = float(position.get('leverage') or 0.)
            if leverage > 0:
                margin = entry * qty / leverage
        if not all(math.isfinite(value) and value > 0
                   for value in (entry, qty, margin, float(price))):
            return None
        from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
        from core.services.exits.peak_trailing_exit import estimated_display_net_pnl

        sign = 1 if side == 'LONG' else -1
        net_pnl = estimated_display_net_pnl(
            entry, float(price), qty, sign, TAKER_FEE_RATE, SLIPPAGE_PCT,
        )
        return net_pnl / margin * 100.0
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _ratchet_lock_details(position, quote):
    side = str((position or {}).get('side', '')).upper()
    if side not in ('LONG', 'SHORT'):
        return None
    try:
        entry = float(position.get('entry_price') or 0.)
        favorable = (
            max(float(position.get('highest_price') or entry), float(quote))
            if side == 'LONG'
            else min(float(position.get('lowest_price') or entry), float(quote))
        )
        peak_roe = _net_roe_pct(position, favorable, side)
        current_roe = _net_roe_pct(position, quote, side)
        if peak_roe is None or current_roe is None:
            return None
        saved_state = position.get('three_tier_net_roe_lock_state')
        if isinstance(saved_state, dict):
            saved_peak = float(saved_state.get('peak_net_roe_pct') or peak_roe)
            if math.isfinite(saved_peak):
                peak_roe = max(peak_roe, saved_peak)
        floor, tier = tiered_net_roe_floor(peak_roe)
        if floor is None:
            return None
        if not has_reached_net_roe_floor(current_roe, floor):
            return None
        return {
            'peak_net_roe_pct': peak_roe,
            'current_net_roe_pct': current_roe,
            'floor_net_roe_pct': floor,
            'tier': tier,
            'peak_roe': peak_roe / 100.0,
            'giveback_ratio': max(0.0, (peak_roe - current_roe) / peak_roe)
            if peak_roe > 0 else 0.0,
        }
    except (TypeError, ValueError, OverflowError):
        return None


def _calculate_bars_held(position: Dict[str, Any], frame: Optional[pd.DataFrame]) -> int:
    """Calculate the number of bars a position has been held."""
    if not position:
        return 999
    if 'bars_held' in position and position['bars_held'] is not None:
        try:
            return int(position['bars_held'])
        except (ValueError, TypeError):
            pass
    open_ts = position.get('open_timestamp')
    if open_ts is not None and frame is not None and not frame.empty:
        try:
            curr_bar = frame.iloc[-1]
            curr_ts = float(curr_bar.get('timestamp') or 0.0)
            open_sec = float(open_ts)
            open_ms = open_sec * 1000.0 if open_sec < 1e11 else open_sec
            tf_ms = float(getattr(frame, 'attrs', {}).get('timeframe_ms', 60000) or 60000)
            if curr_ts > 0 and open_ms > 0 and tf_ms > 0:
                diff_ms = max(0.0, curr_ts - open_ms)
                return max(1, int(diff_ms // tf_ms) + 1)
        except (ValueError, TypeError, OverflowError):
            pass
    open_bar_id = position.get('open_bar_id') or position.get('entry_bar_id') or position.get('candidate_bar_id')
    if open_bar_id is not None and frame is not None and not frame.empty:
        try:
            curr_bar = frame.iloc[-1]
            curr_ts = float(curr_bar.get('timestamp') or 0.0)
            tf_ms = float(getattr(frame, 'attrs', {}).get('timeframe_ms', 60000) or 60000)
            if curr_ts > 0 and float(open_bar_id) > 0 and tf_ms > 0:
                diff_ms = max(0.0, curr_ts - float(open_bar_id))
                return max(1, int(diff_ms // tf_ms) + 1)
        except (ValueError, TypeError, OverflowError):
            pass
    return 999


class HoldingProtectionExitGate:
    """Gatekeeper ensuring position is never closed on normal pullbacks."""

    REJECT_REASON = "REJECT_EXIT: BLOCKED_BY_HOLDING_PROTECTION_GATE (Normal Pullback Breathing)"
    REJECT_HEALTHY_PULLBACK = "REJECT_EXIT: BLOCKED_BY_HEALTHY_PULLBACK"
    WAIT_CLOSE_REJECT_REASON = (
        "REJECT_EXIT: BLOCKED_BY_HOLDING_PROTECTION_GATE "
        "(Wait Bar Close Confirmation)"
    )
    SHORT_BREAK_HOLD_REJECT_REASON = (
        "REJECT_EXIT: SHORT_LOWER_BREAK_AWAITING_35PCT_REBOUND_OR_BULLISH_RECLAIM"
    )

    WHITELIST_CODES = {
        'EXIT_BY_EXTREME_WATERFALL',
        'EXIT_BY_CIRCUIT_BREAKER_HARD_SL',
        'EXIT_BY_RATCHET_PROFIT_LOCK',
        'EMERGENCY_DUMP_EXIT',
        'TWO_BAR_DOJI_REVERSAL_EXIT',
        'EMERGENCY_EXTREME_ADVERSE_BAR',
        'CANDLE_CLOSE_MA15_BREAK',
        INTRABAR_SPIKE_REBOUND_EXIT,
        'EXIT_LONG_ON_TOP_WATERFALL_DUMP',
        'EXIT_BY_VERIFIED_FRACTAL_PEAK',
        'EXIT_BY_VERIFIED_FRACTAL_VALLEY',
        'EXIT_SHORT_ON_EXHAUSTION_REVERSAL',
        'EXIT_LONG_ON_EXHAUSTION_REVERSAL',
        # Aliases
        'EXIT_LONG_ON_FRACTAL_PEAK',
        'EXIT_SHORT_ON_FRACTAL_VALLEY',
        'EXIT_LONG_ON_SELLING_PRESSURE',
        'EXIT_SHORT_ON_BUYING_PRESSURE',
    }

    @classmethod
    def evaluate(cls, position: Dict[str, Any], frame: Optional[pd.DataFrame],
                 quote: float, channel_swing: bool = False) -> Tuple[Optional[str], Dict[str, Any]]:
        """Directly evaluates active market data for the 4 whitelisted exit scenarios."""
        if not position or quote <= 0:
            return None, {}

        side = str(position.get('side', '')).upper()
        entry_price = float(position.get('entry_price', 0.0))
        if entry_price <= 0:
            return None, {}

        if (channel_swing
                or str(position.get('entry_mode') or '').upper() == 'CHANNEL_SWING'):
            return None, {}

        highest_price = float(position.get('highest_price') or position.get('peak_price') or entry_price)
        lowest_price = float(position.get('lowest_price') or position.get('trough_price') or entry_price)
        if quote > highest_price:
            highest_price = quote
        if quote < lowest_price:
            lowest_price = quote

        has_frame = frame is not None and len(frame) > 0
        curr = frame.iloc[-1] if has_frame else {}
        prev = frame.iloc[-2] if (frame is not None and len(frame) > 1) else {}
        atr = float(prev.get('atr', curr.get('atr', 0.0))) if has_frame else 0.0
        candle_closed = _current_bar_is_closed(frame)

        # ── 1. 破軌多單進場保護期 (Grace Period: bars_held <= 2) ──
        # 開倉後前 2 根 K 棒僅在跌破開倉停損底線（Stop Loss）時出場，避免被微幅盤中影線早洗。
        bars_held = _calculate_bars_held(position, frame)
        if side == 'LONG' and bars_held <= 2:
            sl = float(position.get('sl') or position.get('initial_sl') or 0.0)
            if sl <= 0 and atr > 0:
                sl = entry_price - 1.5 * atr
            if sl > 0 and quote <= sl:
                return 'EXIT_BY_CIRCUIT_BREAKER_HARD_SL', {
                    'stop_loss': sl, 'quote': quote, 'bars_held': bars_held,
                    'side': side, 'reason': 'STOP_LOSS_HIT_IN_GRACE_PERIOD',
                }

        # ── 1.1 極端異常：大瀑布反轉 / 極速反撲 (EXIT_BY_EXTREME_WATERFALL) ──
        if frame is not None and len(frame) >= 1 and atr > 0:
            curr_open = float(curr.get('open', quote))
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))
            kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))

            if side == 'LONG':
                adverse_body = curr_open - quote
                if (not candle_closed and adverse_body >= 1.5 * atr
                        and kc_middle > 0 and quote < kc_middle):
                    return 'EXIT_BY_EXTREME_WATERFALL', {
                        'adverse_body': adverse_body, 'atr': atr,
                        'kc_middle': kc_middle,
                        'quote': quote, 'side': side,
                    }
            elif side == 'SHORT':
                adverse_body = quote - curr_open
                if (not candle_closed and adverse_body >= 1.5 * atr
                        and kc_middle > 0 and quote > kc_middle):
                    return 'EXIT_BY_EXTREME_WATERFALL', {
                        'adverse_body': adverse_body, 'atr': atr,
                        'kc_middle': kc_middle,
                        'quote': quote, 'side': side,
                    }

        if side in ('LONG', 'SHORT'):
            spike_rebound = intrabar_spike_rebound_evidence(
                position, frame, side, quote,
            )
            if spike_rebound is not None:
                return INTRABAR_SPIKE_REBOUND_EXIT, spike_rebound

        # ── 1.2 頂部斷頭鍘特例 (Top Waterfall Flip) 授權「平多 + 秒反手開空」──
        if side == 'LONG' and candle_closed:
            evidence = top_engulfing_flip_evidence(frame)
            if evidence is not None:
                position.pop('flip_enter_short', None)
                details = {**evidence, 'quote': quote, 'side': side}
                if evidence['flip_short_allowed']:
                    position['flip_enter_short'] = True
                    details.update(
                        flip_enter_short=True,
                        authorized_action='ACTION_FLIP_LONG_TO_SHORT',
                    )
                return 'EXIT_LONG_ON_TOP_WATERFALL_DUMP', details

        exhaustion = exhaustion_reversal_evidence(frame, side)
        if exhaustion is not None:
            favorable_price = highest_price if side == 'LONG' else lowest_price
            favorable_move = (
                favorable_price - entry_price if side == 'LONG'
                else entry_price - favorable_price
            )
            favorable_roe = favorable_move / entry_price if entry_price > 0 else 0.0
            if (atr > 0 and (favorable_roe >= 0.03 or favorable_move >= 0.5 * atr)):
                closed_reversal = closed_peak_valley_reversal_evidence(frame, side, atr)
                if closed_reversal is not None:
                    exhaustion.update(closed_reversal)
                    exhaustion.update(
                        quote=quote, closed_reversal_confirmation=True,
                        peak_roe=favorable_roe if side == 'LONG' else 0.0,
                        valley_roe=favorable_roe if side == 'SHORT' else 0.0,
                        peak_gain_atr=favorable_move / atr,
                    )
                    return f'EXIT_{side}_ON_EXHAUSTION_REVERSAL', exhaustion

        # ── 3. 階梯鎖利：以淨 ROE 峰值計算保護底線 ──
        # 鐵律：開倉後，不要平倉，要到真峰谷才平倉！未成型真峰谷前，禁止觸發階梯鎖利平倉！
        ratchet = _ratchet_lock_details(position, quote)
        if ratchet is not None:
            pressure = intrabar_adverse_pressure_evidence(frame, side, quote, atr)
            if pressure is not None:
                has_pivot, _, _ = has_confirmed_true_pivot(position, frame, quote)
                if has_pivot:
                    ratchet.update(
                        highest_price=highest_price, lowest_price=lowest_price,
                        entry_price=entry_price,
                        **pressure,
                    )
                    return 'EXIT_BY_RATCHET_PROFIT_LOCK', ratchet

        midline_reversal = confirmed_midline_reversal_evidence(frame, side)
        if midline_reversal is not None:
            return f'EXIT_{side}_ON_CONFIRMED_MIDLINE_BREAK', midline_reversal

        if side == 'SHORT':
            bottom = confirmed_short_bottom_evidence(frame)
            if bottom is not None:
                return 'EXIT_SHORT_ON_CONFIRMED_TROUGH', bottom

        # ── 4. 結構確認：真實波段峰頂 / 谷底生成 (EXIT_BY_VERIFIED_FRACTAL_PEAK / VALLEY) ──
        if (not candle_closed and side == 'SHORT' and atr > 0
                and frame is not None and len(frame) > 0):
            live_open = float(curr.get('open', quote))
            live_high = max(float(curr.get('high', quote)), quote)
            upper_wick = live_high - max(live_open, quote)
            if upper_wick >= 0.4 * atr:
                return None, {
                    'upper_wick_hold': True, 'upper_wick': upper_wick,
                    'atr': atr, 'quote': quote,
                }

        if candle_closed and frame is not None and len(frame) >= 3:
            prev_open = float(prev.get('open', 0.0))
            prev_close = float(prev.get('close', 0.0))
            prev_mid = (prev_open + prev_close) / 2.0

            if side == 'LONG':
                peak_roe = (highest_price - entry_price) / entry_price
                peak_gain_atr = (highest_price - entry_price) / atr if atr > 0 else 0.0
                has_meaningful_profit = _fractal_pullback_exit_authorized(
                    position, frame, side, quote, atr, highest_price - entry_price,
                )

                if has_meaningful_profit and is_fractal_peak(frame):
                    # 必須為收線實體跌破前棒實體中點（非影線虛破）
                    curr_open = float(curr.get('open', quote))
                    curr_close = float(curr.get('close', quote))
                    effective_price = curr_close if candle_closed else quote

                    # 多單遭遇長下影線下探回升，嚴禁平倉
                    curr_low = float(curr.get('low', quote))
                    lower_wick = min(curr_open, effective_price) - curr_low
                    if atr > 0 and lower_wick >= 0.4 * atr:
                        return None, {'lower_wick_hold': True}

                    if (effective_price < prev_mid
                            and _long_reversal_body_evidence(frame, quote, atr) is not None):
                        return 'EXIT_BY_VERIFIED_FRACTAL_PEAK', {
                            'peak_roe': peak_roe, 'prev_mid': prev_mid,
                            'effective_price': effective_price, 'quote': quote,
                            'atr': atr, 'closed_reversal_confirmation': True,
                            'peak_gain_atr': peak_gain_atr,
                        }

            elif side == 'SHORT':
                peak_roe = (entry_price - lowest_price) / entry_price
                peak_gain_atr = (entry_price - lowest_price) / atr if atr > 0 else 0.0
                has_meaningful_profit = _fractal_pullback_exit_authorized(
                    position, frame, side, quote, atr, entry_price - lowest_price,
                )
                if candle_closed and has_meaningful_profit:
                    live = frame.iloc[-1]
                    curr_open = float(live.get('open', quote))
                    curr_close = float(live.get('close', quote))
                    curr_low = float(live.get('low', quote))
                    body = abs(curr_close - curr_open)
                    lower_wick = min(curr_open, curr_close) - curr_low
                if (has_meaningful_profit and _current_bar_is_closed(frame)
                        and is_fractal_valley(frame)):
                    body_evidence = _short_reversal_body_evidence(frame, quote, atr)
                    if body_evidence is not None:
                        body_evidence.update(
                            valley_roe=peak_roe, peak_gain_atr=peak_gain_atr,
                            quote=quote,
                        )
                        return 'EXIT_BY_VERIFIED_FRACTAL_VALLEY', body_evidence
                    live = frame.iloc[-1]
                    effective = float(live.get('close', quote))
                    upper_wick = float(live.get('high', effective)) - max(
                        float(live.get('open', effective)), effective,
                    )
                    if atr > 0 and upper_wick >= 0.4 * atr:
                        return None, {'upper_wick_hold': True, 'upper_wick': upper_wick}

        return None, {}

    @classmethod
    def validate_exit(cls, position: Dict[str, Any], frame: Optional[pd.DataFrame],
                      quote: float, candidate_reason: str,
                      details: Optional[Dict[str, Any]] = None,
                      channel_swing: bool = False) -> Tuple[bool, str, Dict[str, Any]]:
        """Strict whitelist verification for any proposed exit reason.
        
        Returns:
            (is_allowed: bool, authorized_reason_or_reject_code: str, details: dict)
        """
        details = details or {}
        side = str((position or {}).get('side', '')).upper()
        clean_reason = str(candidate_reason or '').replace('Channel Swing ', '').strip()
        candle_closed = _current_bar_is_closed(frame)

        import logging
        logger = logging.getLogger("uvicorn.error")
        symbol = (position or {}).get('symbol', 'UNKNOWN')
        frame_atr = 0.0
        try:
            if frame is not None and len(frame) > 0:
                live = frame.iloc[-1]
                previous = frame.iloc[-2] if len(frame) > 1 else live
                frame_atr = float(previous.get('atr', live.get('atr', 0.0)))
        except (AttributeError, TypeError, ValueError, IndexError, OverflowError):
            frame_atr = 0.0

        def _auth(code):
            auth_msg = f"[HOLDING_PROTECTION] Authorized exit for {symbol}: {code}"
            logger.info(auth_msg)
            print(auth_msg, flush=True)
            return True, code, details

        def _reject(wait_for_close=False, reason=None):
            reason = reason or (
                cls.WAIT_CLOSE_REJECT_REASON if wait_for_close
                else cls.REJECT_REASON
            )
            reject_msg = f"[HOLDING_PROTECTION] Rejected exit for {symbol}: {reason}"
            logger.info(reject_msg)
            print(reject_msg, flush=True)
            return False, reason, details

        hard_exit = any(marker in clean_reason.upper() for marker in (
            'HARD_STOP', 'CIRCUIT_BREAKER', 'HARD_SL', 'ACCOUNT_HARD_STOP',
            'EMERGENCY_STOP', 'EXIT_BY_CIRCUIT_BREAKER_HARD_SL',
        ))
        is_profit_lock = _is_profit_lock_reason(clean_reason)

        if side == 'SHORT' and not hard_exit and not is_profit_lock:
            lower_break = short_lower_break_exit_confirmation(
                frame, quote, position,
            )
            if lower_break['active'] and not lower_break['confirmed']:
                details.update(short_lower_break_hold=lower_break)
                return _reject(reason=cls.SHORT_BREAK_HOLD_REJECT_REASON)

        if hard_exit:
            return _auth('EXIT_BY_CIRCUIT_BREAKER_HARD_SL')

        if is_profit_lock:
            lock = _verified_profit_lock_details(position, quote)
            if lock is None:
                return _reject(reason='VETO_PROFIT_LOCK_NOT_REACHED')
            details.update(lock)
            return _auth(clean_reason)

        is_safety_exit = any(s in clean_reason.upper() for s in (
            'HARD_STOP', 'CIRCUIT_BREAKER', 'HARD_SL', 'ACCOUNT_HARD_STOP',
            'EMERGENCY_STOP', 'EXTREME_WATERFALL', 'CATASTROPHIC_DUMP',
            'CATASTROPHIC_PUMP', 'OPPOSITE_KC_BAND_BREACH',
            'EXIT_LONG_ON_KC_LOWER_BREACH', 'EXIT_SHORT_ON_KC_UPPER_BREACH',
        ))

        # 妖幣專用 KC 外軌鎖定：只要價格維持在 KC 外軌外且守穩 MA5，一律死抱利潤，拒絕被洗盤震下車！
        from core.gates.meme_coin_gate import MemeCoinGate
        if MemeCoinGate.is_meme_coin(symbol):
            should_hold, hold_reason = MemeCoinGate.should_hold_outer_rail(
                frame, quote, side, symbol=symbol,
            )
            if should_hold and not hard_exit and not is_profit_lock and clean_reason not in (
                'EXIT_BY_EXTREME_WATERFALL', 'EMERGENCY_DUMP_EXIT',
                'EMERGENCY_EXTREME_ADVERSE_BAR',
            ):
                return _reject(reason=hold_reason)

        is_profit_lock = _is_profit_lock_reason(clean_reason)
        has_pivot, pivot_type, pivot_info = has_confirmed_true_pivot(position, frame, quote)
        is_pivot_candidate = any(p in clean_reason.upper() for p in (
            'PIVOT', 'FRACTAL_PEAK', 'FRACTAL_VALLEY', 'TRUE_PEAK', 'TRUE_VALLEY', 'TOP_WATERFALL', 'CONFIRMED_TROUGH'
        ))
        is_true_pivot_confirmed = has_pivot and is_pivot_candidate

        # ══════════════════════════════════════════════════════════════════════
        # 【鐵律二：出場端防護：未達 10% 浮盈階段，非收盤不判定，未破中軌/MA15 絕不平倉】
        # ══════════════════════════════════════════════════════════════════════
        entry_p = float((position or {}).get('entry_price', 0.0))
        if entry_p > 0:
            current_profit_pct = (quote - entry_p) / entry_p if side == 'LONG' else (entry_p - quote) / entry_p
        else:
            current_profit_pct = 0.0

        if not hard_exit and not is_safety_exit and not is_profit_lock and not is_true_pivot_confirmed and current_profit_pct < 0.10:
            # 1. 嚴禁使用每秒跳動的即時價平倉，改以「K 棒收盤確認 (Confirmed Bar Close)」為基準
            if not candle_closed:
                return _reject(reason="VETO_HOLDING_UNTIL_BAR_CLOSED")

            # 2. 只要多單收盤價仍高於 KC 中軌 / MA15，全部視為健康回踩，一律強制 VETO 否決平倉！
            curr_bar = frame.iloc[-1] if frame is not None and len(frame) >= 1 else {}
            bar_close_price = float(curr_bar.get('close', quote))
            kc_middle_val = float(curr_bar.get('kc_middle', curr_bar.get('kc_basis', 0.0)))
            ma15_val = float(curr_bar.get('ma15', kc_middle_val))

            if side == 'LONG':
                protect_mid = min(kc_middle_val, ma15_val) if (kc_middle_val > 0 and ma15_val > 0) else (kc_middle_val or ma15_val)
                if protect_mid > 0 and bar_close_price >= protect_mid:
                    return _reject(reason="VETO_HOLDING_HEALTHY_PULLBACK_ABOVE_MID: TREND_INTACT_ABOVE_MA15")
            elif side == 'SHORT':
                protect_mid = max(kc_middle_val, ma15_val) if (kc_middle_val > 0 and ma15_val > 0) else (kc_middle_val or ma15_val)
                if protect_mid > 0 and bar_close_price <= protect_mid:
                    return _reject(reason="VETO_HOLDING_HEALTHY_PULLBACK_BELOW_MID: TREND_INTACT_BELOW_MA15")

        # ──【單邊趨勢全程護航閘門：Gate: StrictEntryAndImmunityGate & TrendHoldingImmunityGate】──
        # 凡任何出場邏輯（包含階梯鎖利、利潤回吐平倉、指標平倉等）欲發出平倉訊號前，必須強制通過此 Gate 審查。
        # 凡趨勢結構未破壞者（多單未跌破 MA15、空單未站上 MA15），一律一票否決平倉（REJECT_EXIT）
        from core.gates.strict_entry_and_immunity_gate import StrictEntryAndImmunityGate
        from core.gates.trend_holding_immunity_gate import TrendHoldingImmunityGate
        if not hard_exit and not is_safety_exit and not is_profit_lock and not is_true_pivot_confirmed and frame is not None and len(frame) >= 1:
            curr_bar = frame.iloc[-1]
            prev_bar = frame.iloc[-2] if len(frame) >= 2 else curr_bar
            curr_c = float(curr_bar.get('close', quote))
            curr_m5 = float(curr_bar.get('ma5', curr_c))
            curr_m15 = float(curr_bar.get('ma15', curr_c))
            prev_m15 = float(prev_bar.get('ma15', curr_m15))
            m15_slope = curr_m15 - prev_m15
            mid_val = float(curr_bar.get('kc_middle', curr_bar.get('kc_basis', 0.0)))

            entry_p = float((position or {}).get('entry_price', 0.0))
            if entry_p > 0:
                net_pnl_pct = (quote - entry_p) / entry_p if side == 'LONG' else (entry_p - quote) / entry_p
            else:
                net_pnl_pct = 0.0

            # 1. 優先使用 StrictEntryAndImmunityGate 檢核
            can_pass_strict, strict_reason = StrictEntryAndImmunityGate().can_exit(
                side,
                {'close': curr_c, 'ma15': curr_m15},
                net_profit_pct=net_pnl_pct,
                is_hard_stop=hard_exit
            )
            if not can_pass_strict:
                return _reject(reason=strict_reason)

            # 2. 趨勢護航二度確認
            can_pass, immunity_reason = TrendHoldingImmunityGate().can_exit(
                side,
                {'close': curr_c, 'open': float(curr_bar.get('open', quote)), 'high': float(curr_bar.get('high', quote)), 'low': float(curr_bar.get('low', quote))},
                {'ma5': curr_m5, 'ma15': curr_m15, 'ma15_slope': m15_slope, 'kc_middle': mid_val},
                net_profit_pct=net_pnl_pct,
                is_hard_stop=hard_exit
            )
            if not can_pass:
                return _reject(reason=immunity_reason)

        # ──【核心鐵律一：開倉後不要當根平倉】──
        # 在開倉當根：除非觸發最高優先硬停損或 10% 淨利鎖利，否則 100% 絕對禁止平倉！
        is_same_bar = is_same_bar_as_entry(position, frame)
        if is_same_bar and not hard_exit:
            if not is_profit_lock:
                return _reject(reason='VETO_SAME_BAR_EXIT_NO_EXIT_ON_ENTRY_BAR')

        # ──【核心鐵律二：真峰谷確認授權放行】──
        # 若已確認成型真峰谷且 candidate 屬於峰谷出場，立即放行平倉！
        if is_true_pivot_confirmed:
            return _auth(clean_reason)

        # ──【核心鐵律三：持倉全週期防洗——除非剛好是真峰谷，否則除了鎖利外都不平倉】──
        if not is_safety_exit and not is_profit_lock:
            # 尚未成型真峰谷，且非鎖利 -> 100% 絕對禁止平倉！
            if side == 'SHORT':
                return _reject(reason="VETO_CLOSE_BELOW_KC_MIDDLE_HOLDING_PROTECTION")
            return _reject(reason='VETO_HOLDING_UNTIL_TRUE_PIVOT')

        # A completed LONG close above KC middle is a hard hold line for
        # ordinary price-action exits. A lower wick at least as large as the
        # body is independent buying-support evidence and also vetoes them.
        # Hard stops, emergency dumps, and an armed profit lock keep priority.
        if (side == 'LONG' and not is_profit_lock
                and not is_pivot_candidate and not has_pivot
                and clean_reason not in (
                    'EXIT_BY_EXTREME_WATERFALL', 'EMERGENCY_DUMP_EXIT',
                    'EMERGENCY_EXTREME_ADVERSE_BAR',
                    'EXIT_BY_CIRCUIT_BREAKER_HARD_SL',
                )):
            try:
                from core.services.candle_data import closed_entry_candles
                closed = closed_entry_candles(frame)
                if not closed.empty:
                    last = closed.iloc[-1]
                    close = float(last['close'])
                    middle = float(last['kc_middle'])
                    wick_bar = frame.iloc[-1] if frame is not None and not frame.empty else last
                    opening = float(wick_bar['open'])
                    wick_close = float(quote)
                    if bool(wick_bar.get('is_closed', True)):
                        wick_close = float(wick_bar['close'])
                    low = min(float(wick_bar['low']), wick_close)
                    # Keep the live wick current with the executable quote;
                    # the close-vs-middle test above remains completed-bar only.
                    body = abs(wick_close - opening)
                    lower_wick = min(opening, wick_close) - low
                    if close >= middle:
                        return _reject(reason='VETO_CLOSE_ABOVE_KC_MIDDLE_HOLDING_PROTECTION')
                    if lower_wick >= body:
                        return _reject(reason='VETO_LONG_LOWER_WICK_BUYING_SUPPORT')
            except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
                pass

        # ──【空頭不衰退嚴禁平倉鐵律（Holding Protection for Short）】──
        if side == 'SHORT' and not is_safety_exit and not is_profit_lock and not is_pivot_candidate and not has_pivot:
            # 1. 檢驗是否收線確認真谷底
            has_pivot, pivot_type, pivot_info = has_confirmed_true_pivot(position, frame, quote)

            # 2. 檢驗是否收盤站上 KC 中軌逆轉
            last_closed_above = _last_closed_close_at_or_above_kc_middle(frame)
            above_kc_mid = (last_closed_above is True) or (frame is not None and len(frame) > 0 and float(quote) > float(frame.iloc[-1].get('kc_middle', 0.0)))

            # 鐵律：若未確認真谷底且未站上 KC 中軌，在空頭走勢中一票否決平空！
            if not has_pivot and not above_kc_mid:
                return _reject(reason="VETO_CLOSE_BELOW_KC_MIDDLE_HOLDING_PROTECTION")

        # One-vote veto: If latest candle closed in favorable KC half
        # (close >= kc_middle for LONG), ordinary pullback exits are strictly vetoed.
        if (
            side == 'LONG'
            and not _is_profit_lock_reason(clean_reason)
            and clean_reason != 'EXIT_BY_EXTREME_WATERFALL'
            and clean_reason not in ('EMERGENCY_DUMP_EXIT', 'EMERGENCY_EXTREME_ADVERSE_BAR')
        ):
            last_closed_above = _last_closed_close_at_or_above_kc_middle(frame)
            if last_closed_above is True:
                return _reject(reason="VETO_CLOSE_ABOVE_KC_MIDDLE_HOLDING_PROTECTION")

        if (clean_reason == 'THREE_POINT_PIVOT'
                or clean_reason.startswith('HYBRID_DYNAMIC')
                or clean_reason == 'KC_MIDLINE_CLOSE_BREACH'
                or clean_reason.startswith('EXIT_MEME_')
                or clean_reason.startswith('EXIT_BY_CONFIRMED_TRUE_')
                or clean_reason in (
                    'MOMENTUM_STALLING_EXIT', 'CANDLE_CLOSE_DUMP_EXIT',
                    'CONFIRMED_MA5_CLOSE_BREAK', 'EXIT_BY_RATCHET_PROFIT_LOCK',
                    'RATCHET_BREAKEVEN_LOCK', 'EMERGENCY_DUMP_EXIT',
                    'TWO_BAR_DOJI_REVERSAL_EXIT', 'EMERGENCY_EXTREME_ADVERSE_BAR',
                    'CANDLE_CLOSE_MA15_BREAK',
                    'EXIT_BY_CONFIRMED_TRUE_VALLEY', 'EXIT_BY_CONFIRMED_TRUE_PEAK',
                )):
            return _auth(clean_reason)

        # MA Slope Shield: 當 MA5 斜率強勢順向（多單 MA5 斜率 > 0.03 ATR 向上，空單 < -0.03 ATR 向下）時，
        # 盤中回踩觸碰 MA5 視為正常抖動，強制「禁止觸發平倉」。
        # 軌道二大賣壓緊急避險豁免斜率保護！
        if (
            not hard_exit
            and clean_reason != 'EXIT_BY_EXTREME_WATERFALL'
            and clean_reason not in ('EMERGENCY_DUMP_EXIT', 'EMERGENCY_EXTREME_ADVERSE_BAR')
        ):
            from core.services.exits.dual_track_exit_service import is_ma5_slope_shield_active
            if is_ma5_slope_shield_active(frame, side, frame_atr):
                return _reject(reason="VETO_MA5_SLOPE_SHIELD_ACTIVE")

        if ((channel_swing
             or str((position or {}).get('entry_mode') or '').upper() == 'CHANNEL_SWING')
                and _is_profit_lock_reason(clean_reason)):
            lock = _verified_profit_lock_details(position, quote)
            if lock is not None:
                details.update(lock)
                return _auth('EXIT_BY_RATCHET_PROFIT_LOCK')
            return _reject(wait_for_close=not candle_closed)

        chop_problem, chop_details = holding_chop_exit_problem(
            position, frame, clean_reason, details,
        )
        if chop_problem:
            details.update(chop_details)
            return _reject(reason=chop_problem)

        if (channel_swing
                or str((position or {}).get('entry_mode') or '').upper() == 'CHANNEL_SWING'):
            return _reject(wait_for_close=not candle_closed)

        if clean_reason == INTRABAR_SPIKE_REBOUND_EXIT:
            evidence = intrabar_spike_rebound_evidence(
                position, frame, side, quote,
            )
            if evidence is None:
                return _reject(wait_for_close=not candle_closed)
            details.update(evidence)
            return _auth(INTRABAR_SPIKE_REBOUND_EXIT)

        if clean_reason == 'EXIT_SHORT_FAILED_KC_BREAKOUT':
            try:
                entry_snapshot = (
                    (position or {}).get('entry_snapshot') or {}
                )
                entry_bar_ms = float(entry_snapshot.get('entry_bar_id') or 0.)
                entry_bar_open = float(entry_snapshot.get('entry_bar_open') or 0.)
                current = frame.iloc[-1]
                previous = frame.iloc[-2]
                current_ms = float(current.get('timestamp') or 0.)
                previous_ms = float(previous.get('timestamp') or 0.)
                live_open = float(current.get('open') or 0.)
                current_quote = float(quote)
                if (
                    side == 'SHORT'
                    and str((position or {}).get('entry_mode') or '').upper() == 'CHANNEL_SWING'
                    and entry_snapshot.get('signal_code') == 'AUTHORIZED_REALTIME_BREAKOUT'
                    and math.isfinite(entry_bar_ms) and entry_bar_ms > 0
                    and math.isfinite(entry_bar_open) and entry_bar_open > 0
                    and math.isfinite(current_ms) and current_ms == entry_bar_ms + 60000
                    and previous_ms == entry_bar_ms
                    and not _current_bar_is_closed(frame)
                    and live_open > 0 and current_quote > live_open
                    and current_quote > entry_bar_open
                ):
                    details.update(
                        entry_bar_id=entry_bar_ms,
                        entry_bar_open=entry_bar_open,
                        live_bar_id=current_ms,
                        live_open=live_open,
                        quote=current_quote,
                    )
                    return _auth(clean_reason)
                return _reject(wait_for_close=not _current_bar_is_closed(frame))
            except (AttributeError, KeyError, TypeError, ValueError,
                    OverflowError, IndexError):
                return _reject()

        # ── 1. 硬止損 / 市場熔斷 (WHITELIST SCENARIO 2) ──
        midline_exempt_reasons = {
            'EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT',
            'CATASTROPHIC_PUMP_EXIT', INTRABAR_SPIKE_REBOUND_EXIT,
        }
        if (not _is_profit_lock_reason(clean_reason)
                and clean_reason not in midline_exempt_reasons
                and confirmed_midline_reversal_evidence(frame, side) is None):
            return _reject(wait_for_close=not candle_closed)

        # ── 1.5 進場保護期 (Grace Period: bars_held <= 2) ──
        bars_held = _calculate_bars_held(position, frame)
        grace_allowed_exits = {
            'EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT',
            'CATASTROPHIC_PUMP_EXIT', 'EXIT_BY_RATCHET_PROFIT_LOCK',
            INTRABAR_SPIKE_REBOUND_EXIT,
            'EXIT_BY_VERIFIED_FRACTAL_PEAK', 'EXIT_LONG_ON_FRACTAL_PEAK',
            'EXIT_BY_VERIFIED_FRACTAL_VALLEY', 'EXIT_SHORT_ON_FRACTAL_VALLEY',
            'EXIT_LONG_ON_SELLING_PRESSURE', 'EXIT_SHORT_ON_BUYING_PRESSURE',
        }
        if (side == 'LONG' and bars_held <= 2
                and clean_reason not in grace_allowed_exits
                and not _is_profit_lock_reason(clean_reason)):
            return _reject()

        # Every intrabar strategy exit waits for candle finality except the
        # verified extreme V-reversal and the ROE ratchet lock.
        if frame is not None and not frame.empty and not _current_bar_is_closed(frame):
            if _is_profit_lock_reason(clean_reason):
                lock = _verified_profit_lock_details(position, quote)
                if lock is not None:
                    details.update(lock)
                    return _auth('EXIT_BY_RATCHET_PROFIT_LOCK')
                return _reject(wait_for_close=True)

            intrabar_peak_valley = (
                clean_reason in (
                    'EXIT_BY_VERIFIED_FRACTAL_PEAK', 'EXIT_LONG_ON_FRACTAL_PEAK',
                    'EXIT_BY_VERIFIED_FRACTAL_VALLEY', 'EXIT_SHORT_ON_FRACTAL_VALLEY',
                ) and details.get('intrabar_pivot_confirmation') is True
            )
            intrabar_catastrophic = clean_reason in (
                'EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT',
                'CATASTROPHIC_PUMP_EXIT',
            )
            intrabar_spike_rebound = (
                clean_reason == INTRABAR_SPIKE_REBOUND_EXIT
                and details.get('intrabar_confirmation') is True
            )
            intrabar_pressure = clean_reason in (
                'EXIT_LONG_ON_SELLING_PRESSURE', 'EXIT_SHORT_ON_BUYING_PRESSURE',
            )
            closed_structure_exit = clean_reason in (
                'EXIT_LONG_ON_CONFIRMED_MIDLINE_BREAK',
                'EXIT_SHORT_ON_CONFIRMED_MIDLINE_BREAK',
                'EXIT_SHORT_ON_CONFIRMED_TROUGH',
            )
            if (not intrabar_peak_valley and not intrabar_catastrophic
                    and not intrabar_pressure and not closed_structure_exit
                    and not intrabar_spike_rebound):
                return _reject(wait_for_close=True)

        if clean_reason in ('EXIT_LONG_ON_SELLING_PRESSURE', 'EXIT_SHORT_ON_BUYING_PRESSURE'):
            # Pressure remains corroborating evidence for ratchet locks, but is
            # never an independent strategy exit under the approved exit policy.
            return _reject(wait_for_close=not candle_closed)

        # ── 2. 極端異常大瀑布：原因標籤不構成授權，重新驗證實體與中軌 ──
        if clean_reason in ('EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT', 'CATASTROPHIC_PUMP_EXIT'):
            try:
                if frame is None or frame.empty or side not in ('LONG', 'SHORT'):
                    return _reject()
                live = frame.iloc[-1]
                previous = frame.iloc[-2] if len(frame) > 1 else live
                atr = float(previous.get('atr', live.get('atr', 0.0)))
                opening = float(live.get('open', quote))
                middle = float(live.get('kc_middle', live.get('kc_basis', 0.0)))
                current_quote = float(quote)
                adverse_body = opening - current_quote if side == 'LONG' else current_quote - opening
                crossed_middle = (
                    current_quote < middle if side == 'LONG' else current_quote > middle
                )
                if (not all(math.isfinite(value) for value in
                            (atr, opening, middle, current_quote, adverse_body))
                        or atr <= 0 or opening <= 0 or middle <= 0
                        or adverse_body < 1.5 * atr or not crossed_middle):
                    return _reject(wait_for_close=not candle_closed)
                details.update(
                    adverse_body=adverse_body, atr=atr, kc_middle=middle,
                    quote=current_quote, side=side,
                )
                return _auth('EXIT_BY_EXTREME_WATERFALL')
            except (KeyError, TypeError, ValueError, OverflowError, IndexError):
                return _reject(wait_for_close=not candle_closed)

        # ── 3. 階梯鎖利回吐 (WHITELIST SCENARIO 3) ──
        if _is_profit_lock_reason(clean_reason):
            lock = _verified_profit_lock_details(position, quote)
            if lock is not None:
                details.update(lock)
                return _auth('EXIT_BY_RATCHET_PROFIT_LOCK')
            return _reject(wait_for_close=not candle_closed)

        if clean_reason in (
            'EXIT_LONG_ON_CONFIRMED_MIDLINE_BREAK',
            'EXIT_SHORT_ON_CONFIRMED_MIDLINE_BREAK',
        ):
            expected = 'LONG' if clean_reason.startswith('EXIT_LONG_') else 'SHORT'
            evidence = confirmed_midline_reversal_evidence(frame, side)
            if side != expected or evidence is None:
                return _reject(wait_for_close=not candle_closed)
            details.update(evidence)
            return _auth(clean_reason)

        if clean_reason == 'EXIT_SHORT_ON_CONFIRMED_TROUGH':
            evidence = confirmed_short_bottom_evidence(frame)
            if side != 'SHORT' or evidence is None:
                return _reject(wait_for_close=not candle_closed)
            details.update(evidence)
            return _auth(clean_reason)

        if clean_reason in (
            'EXIT_SHORT_ON_EXHAUSTION_REVERSAL',
            'EXIT_LONG_ON_EXHAUSTION_REVERSAL',
        ):
            expected_side = (
                'SHORT' if clean_reason == 'EXIT_SHORT_ON_EXHAUSTION_REVERSAL'
                else 'LONG'
            )
            evidence = exhaustion_reversal_evidence(frame, side)
            try:
                atr = float(frame.iloc[-2].get('atr', frame.iloc[-1].get('atr', 0.0)))
            except (AttributeError, TypeError, ValueError, IndexError, OverflowError):
                atr = 0.0
            structural_reversal = closed_peak_valley_reversal_evidence(
                frame, side, atr,
            )
            if (side != expected_side or evidence is None
                    or structural_reversal is None):
                return _reject()
            if side == 'SHORT' and confirmed_short_bottom_evidence(frame) is None:
                return _reject()
            details.update(evidence)
            details.update(structural_reversal)
            return _auth(clean_reason)

        if clean_reason in (
            'EXIT_LONG_ON_UPPER_WICK_REJECTION',
            'EXIT_SHORT_ON_LOWER_WICK_REJECTION',
        ):
            # 拔除影線平倉旁路：盤中影線震盪屬於健康整理，嚴禁平倉
            return _reject(reason="REJECT_EXIT: WICK_REJECTION_EXIT_RETIRED (VETO)")

        # ── 4. 真實分形頂底 (WHITELIST SCENARIO 4) ──
        if clean_reason in (
            'EXIT_BY_VERIFIED_FRACTAL_PEAK', 'EXIT_LONG_ON_FRACTAL_PEAK',
            'EXIT_BY_VERIFIED_FRACTAL_VALLEY', 'EXIT_SHORT_ON_FRACTAL_VALLEY'
        ):
            # 檢查前置利潤條件與防護
            entry_price = float((position or {}).get('entry_price', 0.0))
            highest_price = float((position or {}).get('highest_price') or entry_price)
            lowest_price = float((position or {}).get('lowest_price') or entry_price)

            if side == 'LONG':
                reversal_evidence = confirmed_midline_reversal_evidence(
                    frame, 'LONG',
                )
                if reversal_evidence is None:
                    return _reject(wait_for_close=not candle_closed)
                details.update(reversal_evidence)
                peak_roe = (highest_price - entry_price) / entry_price if entry_price > 0 else 0.0
                atr = float(details.get('atr') or frame_atr or 0.0)
                favorable_move = max(
                    highest_price - entry_price,
                    float(details.get('peak_gain_atr') or 0.0) * atr,
                )
                if not _fractal_pullback_exit_authorized(
                        position, frame, side, quote, atr, favorable_move):
                    return _reject()
                # 多單下影線防護
                if details.get('lower_wick_hold'):
                    return _reject()
                if details.get('intrabar_pivot_confirmation'):
                    evidence = intrabar_peak_valley_reversal_evidence(
                        position, frame, side, float(quote), atr,
                        float(details.get('pivot_price') or 0.0),
                        float(details.get('favorable_move') or 0.0),
                    )
                    if evidence is None:
                        return _reject()
                    details.update(evidence)
                elif details.get('closed_reversal_confirmation'):
                    evidence = closed_peak_valley_reversal_evidence(frame, side, atr)
                    if evidence is None:
                        return _reject()
                    details.update(evidence)
                elif _long_reversal_body_evidence(frame, quote, atr) is None:
                    return _reject()
                authorized_code = 'EXIT_BY_VERIFIED_FRACTAL_PEAK'
            else:
                evidence = confirmed_short_bottom_evidence(frame)
                if evidence is None:
                    return _reject(wait_for_close=not candle_closed)
                details.update(evidence)
                authorized_code = 'EXIT_BY_VERIFIED_FRACTAL_VALLEY'

            return _auth(authorized_code)

        # ── 5. 頂部吞噬斷頭鍘的峰頂確認與原子反手 ──
        if clean_reason == 'EXIT_LONG_ON_TOP_WATERFALL_DUMP':
            if side != 'LONG':
                return _reject()
            if frame is not None and len(frame) >= 2:
                try:
                    evidence = top_engulfing_flip_evidence(frame)
                    if evidence is not None:
                        details.update(evidence)
                        if evidence['flip_short_allowed']:
                            details.update(
                                flip_enter_short=True,
                                authorized_action='ACTION_FLIP_LONG_TO_SHORT',
                            )
                        else:
                            details.pop('flip_enter_short', None)
                            details.pop('authorized_action', None)
                        return _auth('EXIT_LONG_ON_TOP_WATERFALL_DUMP')
                except Exception:
                    pass
            return _reject()

        # ── 6. 健康回踩強制續抱檢驗 (Pullback Immunity) ──
        if frame is not None and len(frame) >= 2:
            try:
                curr_bar = frame.iloc[-1]
                prev_bar = frame.iloc[-2]
                curr_kc_mid = float(curr_bar.get('kc_middle', curr_bar.get('kc_basis', 0.0)))
                prev_kc_mid = float(prev_bar.get('kc_middle', prev_bar.get('kc_basis', 0.0)))
                curr_ma15 = float(curr_bar.get('ma15', 0.0))
                prev_ma15 = float(prev_bar.get('ma15', 0.0))
                ma15_slope = curr_ma15 - prev_ma15
                kc_up = curr_kc_mid > prev_kc_mid
                kc_down = curr_kc_mid < prev_kc_mid

                if side == 'LONG':
                    trend_up = kc_up or ma15_slope > 0
                    above_support = quote >= curr_kc_mid or quote >= curr_ma15
                    if trend_up and above_support:
                        reason = cls.REJECT_HEALTHY_PULLBACK
                        reject_msg = f"[HOLDING_PROTECTION] Rejected exit for {symbol}: {reason}"
                        logger.info(reject_msg)
                        print(reject_msg, flush=True)
                        return False, reason, details
                elif side == 'SHORT':
                    trend_down = kc_down or ma15_slope < 0
                    below_resistance = quote <= curr_kc_mid or quote <= curr_ma15
                    if trend_down and below_resistance:
                        reason = cls.REJECT_HEALTHY_PULLBACK
                        reject_msg = f"[HOLDING_PROTECTION] Rejected exit for {symbol}: {reason}"
                        logger.info(reject_msg)
                        print(reject_msg, flush=True)
                        return False, reason, details
            except Exception:
                pass

        # ── 7. 其餘常規回踩（MA 回碰、影線抖動、未達標轉向等）一律一票否決 ──
        return _reject()
