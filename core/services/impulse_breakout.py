"""Fresh half-ATR body breakouts; no lagging MA/KC-slope authorization."""
import math
from core.services.candle_data import closed_entry_candles

CODES = frozenset({'KC_IMPULSE_BREAKOUT_LONG','KC_IMPULSE_BREAKOUT_SHORT'})
REASONS = {'LONG':'REVERSE_ON_BULLISH_BREAKOUT','SHORT':'REVERSE_ON_BEARISH_BREAKOUT'}


def impulse_entry(frame, quote, symbol='', account=None):
    try:
        closed=closed_entry_candles(frame)
        if len(closed)<3 or bool(frame.iloc[-1].get('is_closed',True)):
            return None
        live=frame.iloc[-1];quote=float(quote)
        if frame.attrs.get('timeframe_ms',60000) != 60000:
            return None
        for _, row in frame.tail(4).iterrows():
            values=[float(row[k]) for k in ('timestamp','open','high','low','close','kc_lower','kc_middle','kc_upper','atr')]
            if not all(math.isfinite(v) and v>0 for v in values):
                return None
            if not (row.kc_lower<row.kc_middle<row.kc_upper
                    and row.low<=min(row.open,row.close) and row.high>=max(row.open,row.close)):
                return None
        if not (frame.tail(4)['timestamp'].diff().dropna()==60000).all():
            return None
        if not math.isfinite(quote) or quote<=0 or float(live.timestamp)!=float(closed.iloc[-1].timestamp)+60000:
            return None
        # Prefer the actually live first impulse; closed impulse is a fallback.
        for source, prior, intrabar in [(live,closed.iloc[-1],True),
                                         (closed.iloc[-1],closed.iloc[-2],False)]:
            values=[float(source[k]) for k in ('timestamp','open','close','kc_lower','kc_middle','kc_upper')]
            atr=float(prior['atr'])
            if not all(math.isfinite(v) and v>0 for v in values+[atr]):
                continue
            lower,middle,upper=map(float,(source.kc_lower,source.kc_middle,source.kc_upper))
            if not lower<middle<upper:continue
            opened=float(source.open);price=quote if intrabar else float(source.close)
            # A fresh opposite close may be followed by a full-channel impulse
            # opening beyond the old rail. Authorize only the next live candle.
            exits = [t for t in getattr(account, 'trades', [])
                     if t.get('symbol') == symbol and t.get('status') == 'CLOSED'
                     and t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')
                     and t.get('id') is not None]
            latest_exit = max(exits, key=lambda t: float(t['id'])) if exits else None
            next_after_exit = bool(intrabar and latest_exit and
                                  float(live.timestamp) == math.floor(float(latest_exit['id'])/60000)*60000+60000)
            bottom_launch = next_after_exit and latest_exit['action'] == 'CLOSE_SHORT' and opened < lower
            top_drop = next_after_exit and latest_exit['action'] == 'CLOSE_LONG' and opened > upper
            side=('LONG' if (lower<=opened<=upper or bottom_launch) and price>upper and price-opened>=.5*atr else
                  'SHORT' if (lower<=opened<=upper or top_drop) and price<lower and opened-price>=.5*atr else None)
            if side is None:continue
            edge=float(live.kc_upper if side=='LONG' else live.kc_lower)
            if not math.isfinite(edge) or edge<=0 or (quote-edge)*(1 if side=='LONG' else -1)<=0:continue
            bar=float(source.timestamp);code='KC_IMPULSE_BREAKOUT_'+side
            evidence=dict(policy='half_atr_impulse_breakout_v1',passed=True,side=side,
                          source_bar_ms=bar,live_bar_ms=float(live.timestamp),intrabar=intrabar,
                          post_close_channel_cross=bool(bottom_launch or top_drop),
                          source_open=opened,source_price=price,body_atr=abs(price-opened)/atr,
                          quote=quote,kc_edge=edge,previous_atr=atr)
            return dict(action='ENTER',side=side,type=code,reason='IMPULSE_BREAKOUT_GATE_PASSED',
                        price=quote,entry_atr=atr,confirmation_bar_id=bar,breakout_bar_id=bar,
                        pair_confirmation_bar_id=float(closed.iloc[-1].timestamp),
                        close_price=float(closed.iloc[-1].close),intrabar=intrabar,
                        entry_phase='IMPULSE_BREAKOUT',strict_gate_evidence=evidence,
                        pending_signal_id=f'{symbol}:{code}:{int(bar)}')
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return None


def reverse_close_fields(position, reason, timestamp_ms):
    pending=position.get('reverse_breakout_pending') or {}
    target=pending.get('side')
    if (target not in REASONS or reason!=REASONS[target]
            or target==position.get('side')):
        return {}
    return dict(reverse_breakout_bar_ms=pending['live_bar_ms'],reverse_entry_side=target,
                reverse_source_open_timestamp=position.get('open_timestamp'))


def reverse_receipt(account,symbol,decision):
    side=decision['side'];live_ms=decision['strict_gate_evidence']['live_bar_ms']
    candidates=[t for t in getattr(account,'trades',[]) if t.get('symbol')==symbol
                and t.get('status')=='CLOSED' and t.get('reason')==REASONS.get(side)
                and t.get('side')!=side and t.get('reverse_entry_side')==side
                and t.get('reverse_breakout_bar_ms')==live_ms
                and not t.get('reverse_entry_consumed')]
    return max(candidates,key=lambda t:t['id']) if candidates else None
