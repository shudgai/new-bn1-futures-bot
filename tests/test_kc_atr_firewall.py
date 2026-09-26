import json
from unittest.mock import Mock

import pandas as pd
import pytest

from core.services.entry_service import strict_kc_entry_gate, check_entry_signals
from core.services.closed_breakout_entry import evaluate_channel_entry
from core.services.exits.entry_atr_protection import initialize_atr_protection, atr_exit_reason
from core.services.exits.profit_protection_service import ProfitProtectionExitStrategy, protection


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def frame_for(side):
    close = 112. if side == 'LONG' else 88.
    return pd.DataFrame([dict(open=100., high=115., low=85., close=close,
                              kc_upper=110., kc_lower=90., atr=2., is_closed=closed)
                         for closed in (True, True, True, False)], index=[-1, 0, 1, 2])


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('close', [100., None, float('nan'), float('inf')])
def test_closed_inside_or_invalid_blocks_every_reason(side, close, monkeypatch):
    frame = frame_for(side)
    frame.loc[1, 'close'] = close
    pivot = Mock(return_value=(True, 'PRIVILEGED', {'action': 'ENTER'}))
    monkeypatch.setattr('core.services.outer_turn_entry.evaluate_outer_turn', pivot)
    assert strict_kc_entry_gate(frame, frame.iloc[-1]['close'], side)
    assert check_entry_signals(frame, side, 0)['action'] == 'WAIT'
    assert not evaluate_channel_entry(frame, frame.iloc[-1]['close'], side)[0]
    pivot.assert_not_called()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_touch_live_retreat_and_extension(side):
    frame = frame_for(side)
    rail = 110 if side == 'LONG' else 90
    sign = 1 if side == 'LONG' else -1
    assert strict_kc_entry_gate(frame, rail, side) == 'STRICT_BLOCK_INSIDE_KC'
    assert strict_kc_entry_gate(frame, rail + sign*4.1, side).startswith('OVEREXTENDED')
    assert strict_kc_entry_gate(frame, rail + sign*2, side) is None
    frame.loc[1, 'close'] = rail
    assert strict_kc_entry_gate(frame, rail + sign*2, side) == 'STRICT_BLOCK_CLOSED_INSIDE_KC'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('atr', [0, -1, float('nan'), float('inf')])
def test_invalid_atr_fails_closed(side, atr):
    frame = frame_for(side)
    frame.loc[1, 'atr'] = atr
    assert strict_kc_entry_gate(frame, frame.iloc[-1]['close'], side) == 'WAIT_INVALID_MARKET_DATA'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('field,reason', [('atr_sl','EXIT_STOP_LOSS'), ('atr_tp','EXIT_TAKE_PROFIT')])
def test_fixed_exit_and_retry_survive_restart_without_frame(side, field, reason):
    p = dict(side=side, entry_price=100., qty=1.)
    initialize_atr_protection(p, 100., side, 2.)
    assert ProfitProtectionExitStrategy().evaluate_exit(p, None, p[field]).startswith(reason)
    restored = json.loads(json.dumps(p))
    assert atr_exit_reason(restored, 100.).startswith(reason)
    assert protection(restored, 100., .0005, .0005)['triggered']


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['paper', 'testnet'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_fill_initializes_persists_and_restores_protection(kind, side, tmp_path, monkeypatch):
    import core.paper_account as pm
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange
    module = pm if kind == 'paper' else tm
    monkeypatch.setattr(module, 'STATE_FILE', str(tmp_path / 'account.json'))
    monkeypatch.setattr(module, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(tm, 'notify_email', lambda *a, **k: None)
    monkeypatch.setattr(module, 'DISABLE_TAKE_PROFIT', True)
    monkeypatch.setattr(module, 'DISABLE_STOP_LOSS', True)
    if kind == 'testnet':
        monkeypatch.setattr(tm.BinanceTestnetAccount, 'credentials_configured', staticmethod(lambda: True))
        account = tm.BinanceTestnetAccount(FakeTestnetExchange())
        await account.initialize()
    else:
        account = pm.PaperAccount()
        account.balance = 1000.
    assert await account.open_position('DOGE/USDT', side, 100., 10., 0., 0., 'TEST', atr=2., leverage=1,
                                       entry_context={'entry_mode': 'CHANNEL_SWING'})
    p = account.positions['DOGE/USDT']
    sign = 1 if side == 'LONG' else -1
    assert p['entry_atr'] == 2.
    assert p['sl'] == pytest.approx(p['entry_price'] - sign*3.)
    assert p['tp'] == pytest.approx(p['entry_price'] + sign*4.)
    assert p['atr_sl'] == p['sl'] and p['atr_tp'] == p['tp']
    if kind == 'paper':
        await account.update_positions({'DOGE/USDT': p['entry_price']})
        assert p['sl'] > 0 and p['tp'] > 0
    account.save_state()
    saved = json.loads((tmp_path/'account.json').read_text())
    assert saved['position_meta']['DOGE/USDT']['entry_atr'] == 2.
    assert saved['position_meta']['DOGE/USDT']['atr_tp'] == p['tp']
    if kind == 'testnet':
        await account.refresh(force=True)
        assert account.positions['DOGE/USDT']['atr_sl'] == p['sl']
    else:
        restored = pm.PaperAccount()
        restored.load_state()
        assert restored.positions['DOGE/USDT']['atr_tp'] == p['tp']


@pytest.mark.anyio
@pytest.mark.parametrize('atr', [0, float('nan'), float('inf'), -1])
async def test_no_paper_fill_without_valid_atr(atr, tmp_path, monkeypatch):
    import core.paper_account as pm
    monkeypatch.setattr(pm, 'STATE_FILE', str(tmp_path/'unused.json'))
    account = pm.PaperAccount()
    before = account.balance
    assert not await account.open_position('DOGE/USDT', 'LONG', 100., 10., 0., 0., 'TEST', atr=atr,
                                           entry_context={'entry_mode':'CHANNEL_SWING'})
    assert account.balance == before and not account.positions


@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('flag',['live_pivot','confirmed_reverse','profit_reentry_token'])
async def test_snapshot_flags_cannot_bypass_closed_gate(side,flag):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from core.engine import TradingEngine
    frame=frame_for(side)
    frame.loc[1,'close']=100.
    engine=SimpleNamespace(fetch_klines=AsyncMock(return_value=pd.concat([frame.iloc[:1],frame],ignore_index=True)),
                           strategy=SimpleNamespace(compute_indicators=lambda f:f),
                           tickers={'X':112. if side=='LONG' else 88.},
                           account=SimpleNamespace(log=Mock()),
                           _channel_intrabar_ready=Mock(return_value=True))
    assert await TradingEngine._fresh_channel_entry_snapshot(engine,'X',side,**{flag:True}) is None
    engine._channel_intrabar_ready.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_scan_can_evaluate_next_bar_without_three_bar_cooldown(side):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from test_closed_breakout_entry import candles
    from core.services.symbol_runner import process_single_symbol_runner
    f=candles(side)
    account=SimpleNamespace(positions={},position_meta={},log=Mock(),channel_profit_reentries={})
    engine=SimpleNamespace(account=account,_channel_exit_frames={},_last_exit_bar_id={'X':180000},
                           get_velocity_drop_ratio=Mock(return_value=0),
                           _execute_confirmed_channel_break=AsyncMock(return_value=False))
    await process_single_symbol_runner(engine,'X',0,None,False,exit_frame=f,exit_quote=113. if side=='LONG' else 87.)
    engine._execute_confirmed_channel_break.assert_awaited_once()
    assert not any('COOLDOWN_3_BARS' in c.args[0] for c in account.log.call_args_list)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_profit_room_required_even_without_mature_trend(side):
    from core.services.entry_room_service import entry_room
    rows=[dict(open=100.,high=101.,low=99.,close=100.,atr=2.,is_closed=i<8) for i in range(9)]
    f=pd.DataFrame(rows)
    assert not entry_room(f,100.,side,.0005,.0001,.0015)['allowed']
    f.loc[3,'high' if side=='LONG' else 'low']=110. if side=='LONG' else 90.
    room=entry_room(f,100.,side,.0005,.0001,.0015)
    assert room['allowed'] and room['checked']
    f.loc[3,'high' if side=='LONG' else 'low']=101.01 if side=='LONG' else 98.99
    assert not entry_room(f,100.,side,.01,.001,.0015)['allowed']


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_outer_ma3_turn_blocks_even_when_price_is_outside(side):
    f=frame_for('LONG')
    f.loc[[-1,0,1],'close']=[116.,114.,112.]
    price=112.
    if side=='SHORT':
        f['close']=200-f['close']; price=200-price
    assert strict_kc_entry_gate(f,price,side) == 'WAIT_MA3_RETURNING_TO_OUTER_RAIL'
    assert strict_kc_entry_gate(f,118. if side=='LONG' else 82.,side).startswith('OVEREXTENDED')


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_post_close_pullback_requires_fresh_observation_and_outer_reclaim(side):
    from test_closed_breakout_entry import candles
    from core.services.closed_breakout_entry import evaluate_channel_entry
    f=candles(side); state={}; closed_at=('180', '180000')
    sign=1 if side=='LONG' else -1
    def check(price,now,identity=closed_at):
        return evaluate_channel_entry(f,price,side,state,'X',now,identity)
    assert not check(100+sign*13,0)[0]
    assert not check(100+sign*9,1)[0]  # Actual pullback into the channel is observed, not bought.
    result=check(100+sign*13,2)
    assert result[0] and result[1] == 'KC_REENTRY_PULLBACK_'+side
    assert not check(100+sign*13,3,('240','240000'))[0]  # New close / same fill bar.
    assert not check(100+sign*13,10)[0]  # A quote gap restarts observations.
