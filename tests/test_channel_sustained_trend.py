from unittest.mock import AsyncMock
import pytest
from core.channel_outer_entry import sustained_trend_ready, aligned_entry, aligned_entry_ready, outside_reentry
from core.engine import TradingEngine
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

def trend(side):
    f=closed_outer_entry_frame('LONG')
    for n,i in enumerate(f.index[-7:-1]):
        f.loc[i,['open','close','kc_middle']]=[101+n*.3,101.25+n*.3,99+n*.1]
    f['high']=f[['open','close']].max(axis=1)+.1
    f['low']=f[['open','close']].min(axis=1)-.1
    f['ma3']=f['close'].rolling(3).mean().fillna(100.)
    f['timestamp']=[(i+1)*60000 for i in range(len(f))]
    if side=='SHORT':
        for k in ('open','close','ma3','ma15','kc_middle','ema_20'): f[k]=200-f[k]
        f['high'],f['low']=200-f['low'].copy(),200-f['high'].copy()
        f['kc_upper'],f['kc_lower']=200-f['kc_lower'].copy(),200-f['kc_upper'].copy()
    return f,float(f.iloc[-1]['close'])

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_directional_trend_passes_all_shared_routes(side):
    f,p=trend(side)
    assert sustained_trend_ready(f,side)
    assert aligned_entry(f,p)['side']==side
    assert aligned_entry_ready(f,p,side)
    assert outside_reentry(f,p,side)['side']==side
    assert TradingEngine._channel_swing_action(f,p)['side']==side

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('case',['wave','overlap','bad_high_low','invalid','short','flat_ck','last_pullback'])
def test_rejects_oscillation_or_missing_confirmation(side,case):
    f,p=trend(side); sign=1 if side=='LONG' else -1
    if case=='wave': f.loc[f.index[-7:-4],'kc_middle']=[100,99,100]
    if case=='flat_ck': f.loc[f.index[-6],'kc_middle']=f.iloc[-7]['kc_middle']
    if case=='overlap':
        f.loc[f.index[-7:-1],'open']=f.loc[f.index[-7:-1],'close']-sign*3
        rail='kc_upper' if side=='LONG' else 'kc_lower'
        f.loc[f.index[-3],'open']=float(f.iloc[-3][rail])+sign*.01
        f['high']=f[['open','close']].max(axis=1)+.1
        f['low']=f[['open','close']].min(axis=1)-.1
    if case=='bad_high_low': f.loc[f.index[-5],'low']=f.iloc[-5]['high']+1
    if case=='invalid': f.loc[f.index[-5],'close']=float('nan')
    if case=='short': f=f.tail(6)
    if case=='last_pullback':
        f.loc[f.index[-2],['high','low']]=f.iloc[-3][['high','low']].to_numpy()
    assert not sustained_trend_ready(f,side)
    assert not aligned_entry_ready(f,p,side)
    assert outside_reentry(f,p,side)['action']=='WAIT'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_candle_cannot_confirm_or_destroy_closed_trend(side):
    f,p=trend(side)
    f.loc[f.index[-1],['open','close','high','low','kc_middle']]=float('nan')
    assert sustained_trend_ready(f,side)
    f.loc[f.index[-5],'kc_middle']=float('nan')
    assert not sustained_trend_ready(f,side)

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('cached',[False,True])
@pytest.mark.parametrize('reentry',[False,True])
async def test_wave_cannot_bypass_order_validation(side,cached,reentry,monkeypatch):
    f,p=trend(side);f.loc[f.index[-7:-4],'kc_middle']=[100,99,100]
    e=_execution_engine(f,side,True);e.account.positions.clear();e.tickers[SYMBOL]=p
    snapshot=dict(frame=f,price=p,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    e._fresh_channel_entry_snapshot=AsyncMock(return_value=snapshot)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    signal=dict(side=side,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',signal_code='KC_CONTINUATION_'+side)
    if reentry: signal['profit_reentry_token']='closed-profit'
    assert not await e._place_structured_entry(SYMBOL,signal,p,channel_snapshot=snapshot if cached else None)
    assert not e.account.events

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_confirmed_outer_breakout_can_enter_without_six_bar_trend(side):
    f=closed_outer_entry_frame(side);p=float(f.iloc[-1]['close'])
    assert not sustained_trend_ready(f,side)
    assert aligned_entry_ready(f,p,side)
    assert aligned_entry(f,p)['side']==side
    assert outside_reentry(f,p,side)['side']==side
    assert TradingEngine._channel_swing_action(f,p)['side']==side

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('invalid',['one_body','inside','opposing_ma3','abnormal'])
def test_breakout_exception_retains_entry_risk(side,invalid):
    f=closed_outer_entry_frame(side);p=float(f.iloc[-1]['close']);sign=1 if side=='LONG' else -1
    if invalid=='one_body': f.loc[f.index[-3],'open']=f.iloc[-3]['close']
    if invalid=='inside': p=float(f.iloc[-1]['kc_middle'])
    if invalid=='opposing_ma3': p=float(f.iloc[-4]['close'])-sign*.1
    if invalid=='abnormal':
        f.loc[f.index[-1],'open']=p+sign*1.1
        f['high']=f[['open','close']].max(axis=1)+.1
        f['low']=f[['open','close']].min(axis=1)-.1
    assert not aligned_entry_ready(f,p,side)
