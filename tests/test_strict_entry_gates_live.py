"""Exercise every automatic trigger and the final account boundary gates."""
import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services import entry_contract
from core.services.entry_firewall import validate_account_entry
from core.services.strict_entry_gates import validate_strict_entry


def frame_for(side='LONG'):
    stamp = int(time.time() // 60) * 60000
    rows = []
    for i in range(60):
        mid = 100 + i * .001
        rows.append(dict(timestamp=stamp-(60-i)*60000, open=100., close=100.1,
                         high=101., low=99., atr=1., ma3=100., ma5=100.4,
                         ma15=99., kc_middle=mid, kc_lower=mid-2, kc_upper=mid+2,
                         is_closed=True))
    rows[30]['high'] = 110.
    rows[-1].update(open=102., close=103., high=103.2, low=101.9)
    rows.append(dict(timestamp=stamp, open=101., close=103., high=103.1, low=100.9,
                     atr=1., ma3=102., ma5=100.5, ma15=99., kc_middle=100.06,
                     kc_lower=98.06, kc_upper=102.06, is_closed=False))
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        source = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma5','ma5'),('ma15','ma15'),
                    ('kc_middle','kc_middle'),('kc_upper','kc_lower'),('kc_lower','kc_upper')]:
            f[a] = 200-source[b]
    f.attrs.update(timeframe_ms=60000, entry_finality_verified=True,
                   entry_finality_server_ms=time.time()*1000)
    return f


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_valid_symmetric_gate_and_recorded_costs(side):
    f = frame_for(side)
    passed, reason, evidence = validate_strict_entry(f, f.iloc[-1].close, side)
    assert passed, reason
    assert evidence['passed']
    assert evidence['body_atr'] >= .5
    assert 0 < evidence['distance_atr'] <= 3
    assert evidence['net_room'] >= .0015


@pytest.mark.parametrize('code', ['TRIGGER_A_KC_BREAKOUT','TRIGGER_B_MA_CROSS',
                                 'TRIGGER_C_CONTINUATION','RE_ENTRY_LONG'])
@pytest.mark.parametrize('failure', ['inside','body','ma5','narrow','ma_spacing',
                                     'rail_spacing','no_target','bad_data','gap','distance'])
def test_no_trigger_can_bypass_strict_gates(code, failure, monkeypatch):
    f = frame_for()
    quote = 103.
    changes = {
        'inside': {'close':101.}, 'body': {'open':102.8}, 'ma5': {'ma5':100.4},
        'ma_spacing': {'ma15':100.4}, 'rail_spacing': {'ma5':102.},
        'bad_data': {'ma5':float('nan')}, 'gap': {'open':97.},
    }
    for key,value in changes.get(failure,{}).items():
        f.loc[f.index[-1],key] = value
    if failure == 'inside': quote=101.
    if failure == 'distance': quote=106.
    if failure == 'narrow':
        f.loc[f.index[-5], ['kc_lower','kc_upper']] = [80.,120.]
    if failure == 'no_target': f.loc[f.index[30],'high'] = 101.
    monkeypatch.setattr(entry_contract, 'detect_raw_triggers', lambda *a:("LONG",code))
    diagnostics = {}
    assert entry_contract.evaluate_entry_contract(f, quote, code, diagnostics=diagnostics) is None
    assert diagnostics['reason'].startswith('BLOCKED_STRICT_')
    assert diagnostics['strict_gate_evidence']['passed'] is False


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_same_side_continuation_requires_prior_outside_and_ck(side):
    f = frame_for(side)
    sign = 1 if side=='LONG' else -1
    f.loc[f.index[-1],'open'] = 100+sign*2.3
    assert validate_strict_entry(f,100+sign*3,side)[0]
    f.loc[f.index[-2], ['open','close','high','low']] = [100.,100.,104.,96.]
    assert validate_strict_entry(f,100+sign*3,side)[1] == 'BLOCKED_STRICT_PREVIOUS_NOT_OUTSIDE'
    f = frame_for(side)
    f.loc[f.index[-1],'open'] = 100+sign*2.3
    f.loc[f.index[-2],'kc_middle'] = f.iloc[-3].kc_middle
    assert validate_strict_entry(f,100+sign*3,side)[1] == 'BLOCKED_STRICT_CK_DIRECTION'


def test_half_atr_boundary_and_three_atr_cap():
    f = frame_for()
    f.loc[f.index[-1],'open'] = 102.5
    assert validate_strict_entry(f,103.,'LONG')[0]
    f.loc[f.index[-1],'open'] = 102.50001
    assert validate_strict_entry(f,103.,'LONG')[1] == 'BLOCKED_STRICT_BODY_BELOW_HALF_ATR'
    f = frame_for()
    assert validate_strict_entry(f,float(f.iloc[-1].kc_upper)+3,'LONG')[0]
    assert validate_strict_entry(f,float(f.iloc[-1].kc_upper)+3.00001,'LONG')[1] == 'BLOCKED_STRICT_DISTANCE_ABOVE_3_ATR'


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_final_firewall_rechecks_and_saves_gate_evidence(side):
    f = frame_for(side)
    symbol='X/USDT'
    account=SimpleNamespace(positions={},trades=[],entry_frame_provider=AsyncMock(return_value=f))
    d=entry_contract.evaluate_entry_contract(f,account=account,symbol=symbol)
    assert d is not None
    snapshot=dict(symbol=symbol, side=side,signal_code=d['type'],signal_id=d['pending_signal_id'],
                  pending_signal_id=d['pending_signal_id'],candidate_bar_id=d['confirmation_bar_id'],
                  closed_bar=d['confirmation_bar_id'])
    context=dict(entry_signal_code=d['type'],signal_id=d['pending_signal_id'],
                 candidate_bar_id=d['confirmation_bar_id'],channel_confirmation_bar_id=d['confirmation_bar_id'],
                 entry_snapshot=snapshot)
    asyncio.run(validate_account_entry(account,symbol,side,context))
    assert snapshot['strict_gate_evidence']['passed']
    f.loc[f.index[-1],'close']=100.
    f.loc[f.index[-1],'low']=min(100.,float(f.iloc[-1].low))
    f.loc[f.index[-1],'high']=max(100.,float(f.iloc[-1].high))
    with pytest.raises(ValueError,match='BLOCKED_SECOND_THIRD_OUTSIDE_OR_DOJI'):
        asyncio.run(validate_account_entry(account,symbol,side,context))


def test_target_must_be_untouched_and_cost_adjusted(monkeypatch):
    f=frame_for()
    f.loc[f.index[-2],'high']=110.
    assert validate_strict_entry(f,103.,'LONG')[1]=='BLOCKED_STRICT_NO_UNTOUCHED_TARGET'
    f=frame_for()
    monkeypatch.setattr('core.config.TAKER_FEE_RATE', .04)
    assert validate_strict_entry(f,103.,'LONG')[1]=='BLOCKED_STRICT_NET_ROOM'


def test_closed_only_missing_history_and_signal_change_fail_closed():
    f=frame_for()
    assert not validate_strict_entry(f.tail(30),103.,'LONG')[0]
    assert not validate_strict_entry(f.iloc[:-1],103.,'LONG')[0]
    assert entry_contract.evaluate_entry_contract(f,code='RE_ENTRY_SHORT') is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('trigger', ['TRIGGER_A_KC_BREAKOUT','TRIGGER_B_MA_CROSS',
                                    'TRIGGER_C_CONTINUATION','RE_ENTRY'])
def test_engine_to_account_uses_real_strict_gate_and_records_evidence(side, trigger, monkeypatch):
    from unittest.mock import Mock
    from core.engine import TradingEngine
    if trigger == 'RE_ENTRY': trigger += '_' + side
    f=frame_for(side)
    symbol='SUI/USDT'
    monkeypatch.setattr(entry_contract, 'detect_raw_triggers',lambda *a:(side,trigger))
    monkeypatch.setattr('core.config.is_entry_disabled',lambda _:False)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[symbol])
    monkeypatch.setattr('core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',lambda **k:None)
    a=SimpleNamespace(positions={},pending_limit_orders={},trades=[],log=Mock(),
                      get_wallet_balance=lambda:100.,get_available_balance=lambda:100.)
    e=object.__new__(TradingEngine)
    e.account=a
    e.tickers={symbol:float(f.iloc[-1].close)}
    e._entry_boundary_frame=AsyncMock(return_value=f)
    e._execution_price_is_safe=AsyncMock(return_value=True)
    e._abnormal_market_entry_allowed=Mock(return_value=True)
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *a:2.)
    captured={}
    async def opened(**kwargs):
        captured.update(kwargs)
        decision=await validate_account_entry(a,symbol,side,kwargs['entry_context'])
        assert decision['strict_gate_evidence']['passed']
        a.positions[symbol]=dict(side=side)
        return True
    a.open_position=AsyncMock(side_effect=opened)
    signal=dict(side=side,candidate_bar_id=float(f.iloc[-2].timestamp),
                entry_mode='CHANNEL_SWING',signal_code=trigger)
    assert asyncio.run(e._place_structured_entry(symbol,signal,float(f.iloc[-1].close)))
    a.open_position.assert_awaited_once()
    assert captured['entry_context']['entry_snapshot']['strict_gate_evidence']['passed']


def test_target_touched_in_live_candle_is_not_reused():
    f=frame_for()
    f.loc[f.index[-1], 'high']=110.
    assert validate_strict_entry(f,103.,'LONG')[1] == 'BLOCKED_STRICT_NO_UNTOUCHED_TARGET'
