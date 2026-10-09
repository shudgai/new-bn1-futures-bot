"""User-authorized second/third candle entry following a closed body breakout."""
import math
from core.services.candle_data import closed_entry_candles

CODES = frozenset({'KC_SECOND_THIRD_LONG', 'KC_SECOND_THIRD_SHORT'})


def evaluate_second_third(frame, quote, account=None, symbol=''):
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or bool(frame.iloc[-1].get('is_closed', True)):
            return None
        if account is not None and symbol in getattr(account, 'positions', {}):
            return None
        live = frame.iloc[-1]
        quote = float(quote)
        keys = ('timestamp','open','high','low','close','kc_lower','kc_upper','atr')
        for _, row in frame.tail(4).iterrows():
            if not all(math.isfinite(float(row[k])) and float(row[k]) > 0 for k in keys):
                return None
            if not (row.kc_lower < row.kc_upper and row.low <= min(row.open,row.close)
                    and row.high >= max(row.open,row.close)):
                return None
        if not math.isfinite(quote) or quote <= 0:
            return None
        if float(live.timestamp) != float(closed.iloc[-1].timestamp)+60000:
            return None
        high, low = max(float(live.high),quote), min(float(live.low),quote)
        if high <= low or abs(quote-float(live.open))/(high-low) <= .2:
            return None
        for offset in (1,2):
            first = closed.iloc[-offset]
            prior = closed.iloc[-offset-1]
            if float(live.timestamp)-float(first.timestamp) != offset*60000:
                continue
            if float(first.timestamp)-float(prior.timestamp) != 60000:
                continue
            if not float(first.kc_lower) <= float(first.open) <= float(first.kc_upper):
                continue
            side = ('LONG' if first.close > first.kc_upper and first.close > first.open else
                    'SHORT' if first.close < first.kc_lower and first.close < first.open else None)
            if side is None:
                continue
            sign = 1 if side=='LONG' else -1
            rail = float(live.kc_upper if sign==1 else live.kc_lower)
            if sign*(quote-rail) <= 0:
                continue
            if offset == 2:
                second=closed.iloc[-1]
                second_rail=float(second.kc_upper if sign==1 else second.kc_lower)
                if sign*(float(second.close)-second_rail) <= 0:
                    continue
            stamp=float(closed.iloc[-1].timestamp)
            code='KC_SECOND_THIRD_'+side
            signal_id=f'{symbol}:{code}:{int(first.timestamp)}:{int(live.timestamp)}'
            if any((t.get('entry_snapshot') or {}).get('pending_signal_id')==signal_id
                   for t in getattr(account,'trades',[]) if t.get('action','').startswith('OPEN_')):
                return None
            evidence=dict(policy='second_third_outside_non_doji_v1',passed=True,side=side,
                          first_breakout_bar_ms=float(first.timestamp),live_bar_ms=float(live.timestamp),
                          candle_number=offset+1,quote=quote,kc_edge=rail,
                          live_open=float(live.open),body_ratio=abs(quote-float(live.open))/(high-low),
                          doji_max_ratio=.2)
            return dict(action='ENTER',side=side,type=code,reason='SECOND_THIRD_OUTSIDE_NON_DOJI',
                        price=quote,entry_atr=float(closed.iloc[-1].atr),confirmation_bar_id=stamp,
                        breakout_bar_id=float(first.timestamp),pair_confirmation_bar_id=stamp,
                        close_price=float(closed.iloc[-1].close),pending_signal_id=signal_id,
                        entry_phase='SECOND_THIRD_OUTSIDE_NON_DOJI',strict_gate_evidence=evidence)
    except (AttributeError,KeyError,ValueError,TypeError,IndexError,OverflowError):
        return None
