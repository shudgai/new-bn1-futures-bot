"""A doji alone holds; only the immediately following adverse body can close."""
import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON, DOJI_TRIGGER, HARD_REASON, POLICY, STATE_KEY,
    doji_reversal_evidence, evaluate_peak_trailing, migrate_peak_state,
)
from core.services.exits.realtime_profit_exit import cached_tick_indicators, enforce_realtime_profit_exit
from core.services.exits.profit_exit_telemetry import ProfitExitTelemetry


@pytest.fixture(autouse=True)
def disable_exit_telemetry(monkeypatch):
    monkeypatch.setattr(ProfitExitTelemetry, "ENABLED", False)


def sample(side='LONG', bar=180000):
    sign = 1 if side == 'LONG' else -1
    p = dict(side=side, entry_price=100., qty=1., margin=100., leverage=1.,
             open_timestamp=(bar-120000)/1000, entry_atr=1., entry_mode='CHANNEL_SWING')
    p[STATE_KEY] = dict(policy=POLICY, identity=[side,p['open_timestamp'],100.,1.],
                        peak_price=100+sign*2.5, atr=1., peak_net_pnl=2.)
    snap = dict(quote_ms=bar+1000, live_bar_ms=bar, closed_bar_ms=bar-60000,
                live_open=102.5, live_high=102.6, live_low=102., atr=1.,
                last_open=102.25, last_close=102.5, last_high=103., last_low=102.,
                last_kc_upper=103.1, last_kc_lower=96.9)
    if side == 'SHORT':
        old=snap.copy()
        for a,b in [('live_open','live_open'),('live_high','live_low'),('live_low','live_high'),
                    ('last_open','last_open'),('last_close','last_close'),
                    ('last_high','last_low'),('last_low','last_high')]:
            snap[a]=200-old[b]
    return p,snap,100+sign*2.3


@pytest.fixture(autouse=True)
def strong_trend(monkeypatch):
    # Confirmed reversal must execute even while lagging MAs still indicate HOLD.
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
                        lambda *args: ('HOLD', 'TEST'))


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body,allowed',[(0.,False),(.01,False),(.1499,False),(.15,True),(.5,True),(.7,True)])
def test_next_body_atr_boundary(side,body,allowed):
    p,s,price=sample(side);sign=1 if side=='LONG' else -1
    result=evaluate_peak_trailing(p,s['live_open']-sign*body,s)
    assert bool(result)==allowed
    if result:
        assert result['trigger']==DOJI_TRIGGER
        assert p[STATE_KEY]['reversal_body_atr']==pytest.approx(body)
    else:
        assert not p[STATE_KEY].get('pending')


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_topping_shape_requires_near_outer_rail_and_accepts_wick_or_small_body(side):
    p,s,price=sample(side)
    sign=1 if side=='LONG' else -1
    evidence=doji_reversal_evidence(
        s,price,sign,p['entry_price'],p['open_timestamp']*1000,2.5,
        symbol='LOBSTER/USDT',channel_swing=True)
    assert evidence['topping_signal'] is True

    far_from_rail=s.copy()
    if side=='LONG':
        far_from_rail['last_kc_upper']=104.
    else:
        far_from_rail['last_kc_lower']=96.
    assert doji_reversal_evidence(
        far_from_rail,price,sign,p['entry_price'],p['open_timestamp']*1000,
        2.5,symbol='LOBSTER/USDT',channel_swing=True) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_long_rejection_wick_qualifies_with_body_above_35_percent(side):
    p,s,price=sample(side)
    sign=1 if side=='LONG' else -1
    if side=='LONG':
        s.update(last_open=102.,last_close=102.8,last_high=103.6,last_low=101.8,
                 last_kc_upper=103.8,last_kc_lower=96.2)
    else:
        s.update(last_open=98.,last_close=97.2,last_high=98.2,last_low=96.4,
                 last_kc_upper=103.8,last_kc_lower=96.2)
    evidence=doji_reversal_evidence(
        s,price,sign,p['entry_price'],p['open_timestamp']*1000,2.5,
        symbol='LOBSTER/USDT',channel_swing=True)
    assert evidence is not None
    assert evidence['doji_body_ratio'] > .35


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_breaking_previous_extreme_confirms_before_015_atr(side):
    p,s,_=sample(side)
    sign=1 if side=='LONG' else -1
    if side=='LONG':
        s.update(last_open=102.5,last_close=102.5,last_high=103.,last_low=102.45,
                 live_low=102.4)
        price=102.44
    else:
        s.update(last_open=97.5,last_close=97.5,last_high=97.55,last_low=97.,
                 live_high=97.6)
        price=97.56
    evidence=doji_reversal_evidence(
        s,price,sign,p['entry_price'],p['open_timestamp']*1000,2.5,
        symbol='LOBSTER/USDT',channel_swing=True)
    assert evidence is not None
    assert evidence['reversal_body_atr'] < .15
    assert evidence['trigger_price'] == price


@pytest.mark.parametrize('symbol',['SUI/USDT','龙虾/USDT'])
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('is_local_extreme',[True,False])
def test_channel_symbols_do_not_require_the_doji_to_be_a_new_local_extreme(
    symbol, side, is_local_extreme
):
    p,s,price=sample(side)
    sign=1 if side=='LONG' else -1
    p['symbol']=symbol
    if side=='LONG':
        previous_high=102.9 if is_local_extreme else 103.
        previous_low=101.8
    else:
        previous_low=97.1 if is_local_extreme else 97.
        previous_high=98.2
    s['history_outer_pivots']=[
        {'ms':60000.,'h':previous_high,'l':previous_low},
        {'ms':120000.,'h':s['last_high'],'l':s['last_low']},
    ]

    result=evaluate_peak_trailing(p,price,s)

    assert result['trigger']==DOJI_TRIGGER
    assert p[STATE_KEY]['topping_signal'] is True


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['prior_not_topping','prior_flat_range','below_reversal_threshold','same_color',
    'old_closed','old_live','bad_atr','missing_high',
    'invalid_ohlc','nan_prior','doji_is_current'])
def test_doji_alone_and_invalid_sequence_never_close(side,fault,monkeypatch):
    p,s,price=sample(side);sign=1 if side=='LONG' else -1
    import core.services.exits.peak_trailing_exit as exits
    monkeypatch.setattr(exits, "evaluate_symmetric_dual_track_exit", lambda *a, **k: None)
    monkeypatch.setattr(exits, "evaluate_tiered_ratchet_exit", lambda *a, **k: None)
    if fault=='prior_not_topping':
        if side=='LONG':
            s.update(last_open=102.,last_close=102.9,last_high=103.,last_low=102.)
        else:
            s.update(last_open=98.,last_close=97.1,last_high=98.,last_low=97.)
    elif fault=='prior_flat_range':s.update(last_high=s['last_close'],last_low=s['last_close'],last_open=s['last_close'])
    elif fault=='below_reversal_threshold':price=s['live_open']-sign*.149
    elif fault=='same_color':price=s['live_open']+sign*.1
    elif fault=='old_closed':s['closed_bar_ms']-=60000
    elif fault=='old_live':s['live_bar_ms']-=60000
    elif fault=='bad_atr':s['atr']=float('nan')
    elif fault=='missing_high':s.pop('live_high')
    elif fault=='invalid_ohlc':s['last_high']=s['last_low']-.1
    elif fault=='nan_prior':s['last_close']=float('nan')
    elif fault=='doji_is_current':p['open_timestamp']=(s['live_bar_ms']-30000)/1000
    assert evaluate_peak_trailing(p,price,s) is None
    assert not p[STATE_KEY].get('pending')


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_valid_pending_survives_retry_but_obsolete_doji_pending_is_revoked(side):
    p,s,price=sample(side)
    assert evaluate_peak_trailing(p,price,s)['trigger']==DOJI_TRIGGER
    restored=copy.deepcopy(p)
    assert evaluate_peak_trailing(restored,100.,s['quote_ms']+60000)['trigger']==DOJI_TRIGGER
    restored[STATE_KEY].pop('doji_rule_version')
    meta=copy.deepcopy(restored)
    peak=restored[STATE_KEY]['peak_price']
    state=migrate_peak_state(restored,meta)
    assert not state.get('pending')
    assert state['peak_price']==peak
    restored['entry_mode']='TREND'
    restored[STATE_KEY].update(pending=HARD_REASON,trigger='INITIAL_ATR')
    assert migrate_peak_state(restored)['pending']==HARD_REASON


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_waterfall_and_hard_stop_have_priority(side):
    p,s,price=sample(side);sign=1 if side=='LONG' else -1
    assert evaluate_peak_trailing(p,s['live_open']-sign*2.0,s)['trigger']=='WATERFALL_DROP'
    p,s,price=sample(side)
    p['entry_mode']='TREND'
    assert evaluate_peak_trailing(p,100-sign*2.,s)['type']==HARD_REASON


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_tick_uses_closed_doji_then_live_body_and_retries_without_rest(side):
    now=time.time();bar=int(now//60)*60000
    p,s,price=sample(side,bar)
    p['open_timestamp']=(bar-80000)/1000
    p[STATE_KEY]['identity']=[side,p['open_timestamp'],100.,1.]
    symbol='SUI/USDT'
    p['symbol']=symbol
    closed=dict(timestamp=bar-60000,is_closed=True,open=s['last_open'],close=s['last_close'],
                high=s['last_high'],low=s['last_low'],atr=1.,
                kc_upper=103.1 if side=='LONG' else 101.,
                kc_lower=99. if side=='LONG' else 96.9,
                kc_middle=100.)
    earlier=dict(closed,timestamp=bar-120000)
    if side=='LONG':
        earlier['high']=s['last_high']-.1
    else:
        earlier['low']=s['last_low']+.1
    live=dict(timestamp=bar,is_closed=False,open=s['live_open'],close=s['live_open'],
              high=s['live_high'],low=s['live_low'],atr=999.,
              kc_upper=103.1 if side=='LONG' else 103.,
              kc_lower=97. if side=='LONG' else 96.9,
              kc_middle=100.)
    frame=pd.DataFrame([earlier,closed,live])
    snap,atr=cached_tick_indicators(frame,price,now*1000)
    assert atr==1. and snap['closed_bar_ms']==bar-60000
    assert snap['live_high']>=price>=snap['live_low']
    account=SimpleNamespace(positions={symbol:p},position_meta={},save_state=Mock(),log=Mock(),
                            close_position=AsyncMock(return_value=False))
    engine=SimpleNamespace(account=account,is_running=True,_channel_exit_frames={symbol:frame},
                            fetch_klines=AsyncMock(side_effect=AssertionError('No REST')))
    async def run():
        # Merely opening the candle after a doji cannot close.
        assert not await enforce_realtime_profit_exit(engine,symbol,s['live_open'],now*1000)
        account.close_position.assert_not_awaited()
        assert await enforce_realtime_profit_exit(engine,symbol,price,now*1000+1)
        assert DOJI_TRIGGER in account.close_position.await_args.args[2]
        assert account.position_meta[symbol][STATE_KEY]['doji_rule_version']==4
        engine._channel_exit_frames={}
        assert await enforce_realtime_profit_exit(engine,symbol,price,now*1000+2)
        assert account.close_position.await_count==2
        engine.fetch_klines.assert_not_called()
    asyncio.run(run())
