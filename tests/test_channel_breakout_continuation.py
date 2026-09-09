"""Regression for the missed September 9 lobster breakout; no live orders."""
import json
from pathlib import Path
from unittest.mock import AsyncMock
import pandas as pd
import pytest
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, SYMBOL
from test_direct_break_execution import setup_engine

@pytest.fixture
def anyio_backend():
    return 'asyncio'

def historical(bar=1788965280, side='LONG'):
    rows=json.loads((Path(__file__).parent/'fixtures/lobster_breakout_20260909.json').read_text())
    f=pd.DataFrame([r for r in rows if r['time']<=bar])
    f['timestamp']=f['time']*1000
    if side=='SHORT':
        for key in ('open','high','low','close','ma3','ma15','kc_upper','kc_middle','kc_lower'):
            f[key]=1-f[key]
        f['high'],f['low']=f['low'].copy(),f['high'].copy()
        f['kc_upper'],f['kc_lower']=f['kc_lower'].copy(),f['kc_upper'].copy()
    return f

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('bar',[1788965280,1788965340,1788965400])
def test_original_breakout_and_later_continuation_are_eligible(side,bar):
    f=historical(bar,side)
    result=TradingEngine._channel_swing_action(f,float(f.iloc[-1]['close']))
    assert result['action']=='ENTER'
    assert result['side']==side
    if bar==1788965280:
        assert result['reason']=='KC_NEXT_LIVE_PUSH_'+side

@pytest.mark.parametrize('invalid',['opposing','missing','wick','opposite_live','inside','gap','first_only'])
@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_large_breakout_still_requires_valid_successor(side,invalid):
    f=historical(side=side);sign=1 if side=='LONG' else -1
    i=f.index[-1];prev=f.index[-2];price=float(f.iloc[-1]['close'])
    if invalid=='opposing': f.loc[f.index[-4:-1],'ma15']=[.5+sign*.01,.5,.5-sign*.01]
    if invalid=='missing': f.loc[f.index[-3],'ma15']=float('nan')
    if invalid=='wick': f.loc[prev,'open']=float(f.loc[prev,'close'])-sign*.000001
    if invalid=='opposite_live': f.loc[i,'open']=price+sign*.0001
    if invalid=='inside': price=float(f.iloc[-1]['kc_middle'])
    if invalid=='gap': f.loc[i,'open']=float(f.iloc[-2]['close'])+sign*.002
    if invalid=='first_only': f.loc[prev,'close']=f.loc[prev,'open']
    assert TradingEngine._channel_swing_action(f,price)['action']=='WAIT'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_after_opposite_close_uses_new_breakout_bar_and_live_ticker(side):
    f=historical(side=side);price=float(f.iloc[-1]['close'])
    f.loc[f.index[-1],'close']=f.iloc[-1]['open'] # Deliberately stale ticker.
    other='SHORT' if side=='LONG' else 'LONG'
    info={'side':other,'exit_bar_id':1788965220000,'require_new_closed_break':True,'allow_new_outer_signal':True}
    assert not TradingEngine._channel_peak_exit_reentry_blocked('ENTER',False,side,f,info,SYMBOL,live_price=price)
    info['side']=side
    assert TradingEngine._channel_peak_exit_reentry_blocked('ENTER',False,side,f,info,SYMBOL,live_price=price)

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_after_close_execution_submits_once_and_snapshot_accepts(side):
    f=historical(side=side);price=float(f.iloc[-1]['close'])
    e=_execution_engine(f,side,True);e.account.positions.clear()
    e.tickers[SYMBOL]=price;e.account.save_state=lambda:None
    e._channel_swing_peak_exit_info={SYMBOL:dict(side='SHORT' if side=='LONG' else 'LONG',exit_bar_id=1788965220000,require_new_closed_break=True,allow_new_outer_signal=True)}
    e._place_structured_entry=AsyncMock(return_value=True)
    bar=e._channel_candidate_bar_id(f)
    assert await e._fresh_channel_entry_snapshot(SYMBOL,side,bar) is not None
    await e._execute_confirmed_channel_break(SYMBOL,f,price,side)
    await e._execute_confirmed_channel_break(SYMBOL,f,price,side)
    e._place_structured_entry.assert_awaited_once()


def test_later_small_confirmation_does_not_reuse_old_breakout():
    f=historical(1788965520)
    assert TradingEngine._channel_swing_action(f,float(f.iloc[-1]['close']))['action']=='WAIT'


@pytest.mark.anyio
async def test_risk_gate_receives_current_body_and_keeps_rejection(monkeypatch):
    import core.engine as em
    f=historical(1788965340);f['atr']=.001
    price=float(f.iloc[-1]['close'])
    e=_execution_engine(f,'LONG',True);e.account.positions.clear()
    e.tickers[SYMBOL]=price
    monkeypatch.setattr(em,'DEFAULT_SYMBOLS',[SYMBOL])
    captured=[]
    def reject(*args):
        captured.append(args)
        return False
    e._abnormal_market_entry_allowed=reject
    signal={'side':'LONG','score':100,'entry_mode':'CHANNEL_SWING','action':'ENTER_MARKET',
            'candidate_bar_id':e._channel_candidate_bar_id(f)}
    assert not await e._place_structured_entry(SYMBOL,signal,price)
    assert len(captured)==1
    assert captured[0][4:]==(float(f.iloc[-1]['open']),float(f.iloc[-1]['high']),float(f.iloc[-1]['low']),price)
    assert signal['signal_candle_high']==float(f.iloc[-2]['high'])
    assert e.account.events==[]


@pytest.mark.anyio
async def test_calm_successor_fills_paper_order_after_large_closed_candle(setup_engine):
    e,_=setup_engine('LONG')
    f=historical(1788965340);f['atr']=.001
    e.fetch_klines=AsyncMock(return_value=f)
    e.tickers[SYMBOL]=float(f.iloc[-1]['close'])
    e._abnormal_market_entry_allowed=TradingEngine._abnormal_market_entry_allowed.__get__(e)
    e._channel_swing_peak_exit_info={SYMBOL:dict(side='SHORT',exit_bar_id=1788965220000,require_new_closed_break=True,allow_new_outer_signal=True)}
    await e._execute_confirmed_channel_break(SYMBOL,f,e.tickers[SYMBOL],'LONG')
    assert SYMBOL in e.account.positions,e.account.logs[-5:]
    assert e.account.positions[SYMBOL]['side']=='LONG'
    assert len([t for t in e.account.trades if t['action']=='OPEN_LONG'])==1
