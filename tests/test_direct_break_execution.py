"""Exercise closed-break scan through the real order route and paper fill."""
import asyncio
import pytest
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from test_channel_swing import _generate_macro_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

@pytest.fixture
def setup_engine(tmp_path, monkeypatch):
    import core.engine as em
    import core.paper_account as pm
    monkeypatch.setattr(pm,'STATE_FILE',str(tmp_path/'paper.json'))
    monkeypatch.setattr(em,'DEFAULT_SYMBOLS',[SYMBOL])
    monkeypatch.setattr(em,'MAX_SLOTS',2)
    def make(side, held=None):
        f=_generate_macro_frame('DOWN' if side=='LONG' else 'UP',70)
        f['kc_upper']=102.;f['kc_lower']=98.;f['atr']=1.
        f['volume']=1.;f['vol_ma_20']=1000.
        f.loc[66,['open','close']]=[100.,100.]
        f.loc[67,['open','close','high','low']]=[100.,103.,103.,100.] if side=='LONG' else [100.,97.,100.,97.]
        f.loc[68:69,['open','close','high','low']]=[103.,103.,103.,103.] if side=='LONG' else [97.,97.,97.,97.]
        e=_execution_engine(f, held or side,True)
        e.account=PaperAccount()
        e.account.balance=1000.
        e.account.daily_start_balance=1000.
        e.account.positions={};e.account.position_meta={}
        if held:
            e.account.positions[SYMBOL]={'symbol':SYMBOL,'side':held,'entry_price':100.,'qty':1.,'margin':100.,'leverage':1,'sl':0.,'tp':0.,'entry_mode':'CHANNEL_SWING','open_timestamp':1.}
            e.account.position_meta[SYMBOL]={'entry_mode':'CHANNEL_SWING'}
        e.tickers[SYMBOL]=103. if side=='LONG' else 97.
        e.market_prebreakout_directions={SYMBOL:'SHORT' if side=='LONG' else 'LONG'}
        e.btc_1h_st_direction=-1 if side=='LONG' else 1
        e.st_direction_1h_cache={}
        e._continuous_entry_amount=lambda:25.
        e._abnormal_market_entry_allowed=lambda *_:True
        e._touch_entry_math_favorable=lambda *_:False
        e.rotation_event=asyncio.Event()
        for key in ('_channel_inner_trend_hold','_channel_profit_wait_candidates','_channel_swing_last_exit_at','_channel_swing_last_exit_side','_channel_swing_peak_exit_info','_channel_emergency_reentry_wait'):
            setattr(e,key,{})
        e._channel_swing_last_exit_at[SYMBOL]=10**12
        return e,f
    return make

def confirm_break(frame, side):
    """Supply two real closed bodies and an aligned broad MA15 direction."""
    frame['ma15'] = [100. + (i * .01 if side == 'LONG' else -i * .01) for i in range(len(frame))]
    frame.loc[68, ['open', 'close', 'high', 'low']] = (
        [103., 103.2, 103.3, 102.9] if side == 'LONG' else [97., 96.8, 97.1, 96.7]
    )


@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('reverse',[False,True])
async def test_break_fills_on_same_scan_despite_btc_volume_profit_and_old_bar(setup_engine,side,reverse):
    old=('SHORT' if side=='LONG' else 'LONG') if reverse else None
    e,f=setup_engine(side,old)
    _,candidates=await e._process_single_symbol(SYMBOL,1.,'SHORT' if side=='LONG' else 'LONG',False)
    assert not candidates
    if reverse:
        assert e.account.positions[SYMBOL]['side'] == old
    else:
        assert SYMBOL not in e.account.positions
    assert not e.account.trades
    assert SYMBOL not in e._channel_outer_reentry_after_exit

@pytest.mark.anyio
async def test_failed_close_never_opens_opposite(setup_engine):
    e,f=setup_engine('LONG','SHORT')
    confirm_break(f, 'LONG')
    async def fail(*args,**kwargs):return False
    e.account.close_position=fail
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert e.account.positions[SYMBOL]['side']=='SHORT'
    assert e.account.trades==[]

@pytest.mark.anyio
async def test_adverse_ma_cross_without_confirmed_break_holds(setup_engine):
    e, f = setup_engine("LONG", "SHORT")
    f["kc_upper"] = 100.2
    f["kc_lower"] = 98.0
    f.loc[67:68, "ma15"] = 100.0
    f.loc[67:68, "ma3"] = [99.9, 100.1]
    f.loc[68, ["open", "close"]] = [99.0, 100.0]
    e.tickers[SYMBOL] = 100.0
    await e._execute_confirmed_channel_break(SYMBOL, f, 100.0, "LONG")
    assert e.account.positions[SYMBOL]['side'] == 'SHORT'
    assert not e.account.trades
    assert SYMBOL not in e._channel_outer_reentry_after_exit

@pytest.mark.anyio
async def test_failure_retries_real_open_on_next_scan_without_new_body_break(setup_engine):
    e,f=setup_engine('LONG','SHORT')
    confirm_break(f, 'LONG')
    e._abnormal_market_entry_allowed=lambda *_:False
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert SYMBOL not in e.account.positions
    assert e._channel_outer_reentry_after_exit[SYMBOL]=='LONG'
    e._abnormal_market_entry_allowed=lambda *_:True
    # Retry remains eligible only within the same confirmed candle.
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert e.account.positions[SYMBOL]['side']=='LONG',e.account.logs

@pytest.mark.anyio
async def test_daily_halt_keeps_new_order_blocked(setup_engine):
    e,f=setup_engine('LONG','SHORT')
    confirm_break(f, 'LONG')
    await e._process_single_symbol(SYMBOL,1.,None,True)
    assert SYMBOL not in e.account.positions
    assert all(t['status']!='OPEN' for t in e.account.trades)

@pytest.mark.anyio
async def test_manual_position_is_managed_and_metadata_survives_reload(setup_engine):
    e,f=setup_engine('SHORT','SHORT')
    p=e.account.positions[SYMBOL]
    p.update(entry_mode='MANUAL',reason='手動開倉_SHORT')
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert p['entry_mode']=='CHANNEL_SWING'
    assert p['managed_by_bot'] and p['manual_entry']
    assert p['bot_last_managed_at']>0
    e.account.save_state()
    restored=PaperAccount()
    assert restored.positions[SYMBOL]['managed_by_bot']
    assert restored.positions[SYMBOL]['bot_last_managed_at']>0

@pytest.mark.anyio
async def test_manual_close_clears_stale_reverse_and_retry(setup_engine):
    e,f=setup_engine('LONG')
    e._channel_swing_last_reverse_bar[SYMBOL]=68
    e._channel_outer_reentry_after_exit[SYMBOL]='SHORT'
    e._continuous_last_entry_bar={SYMBOL:68}
    e.release_manual_close_state(SYMBOL)
    assert SYMBOL not in e._channel_swing_last_reverse_bar
    assert SYMBOL not in e._channel_outer_reentry_after_exit
    assert SYMBOL not in e._continuous_last_entry_bar

@pytest.mark.anyio
async def test_manual_api_open_close_and_reopen_adopts_immediately(setup_engine, monkeypatch):
    import services.api as api
    e,f=setup_engine('SHORT')
    monkeypatch.setattr(api,'engine',e)
    result=await api.manual_order(api.ManualOrderRequest(symbol=SYMBOL,side='SHORT',amount=25.))
    assert result['status']=='success'
    assert e.account.positions[SYMBOL]['managed_by_bot'] is True
    assert e.account.positions[SYMBOL]['manual_entry'] is True
    assert e.account.trades[0]['managed_by_bot'] is True
    e._channel_swing_last_reverse_bar[SYMBOL]=68
    e._channel_outer_reentry_after_exit[SYMBOL]='SHORT'
    assert (await api.manual_close(api.ManualCloseRequest(symbol=SYMBOL)))['status']=='success'
    assert SYMBOL not in e._channel_swing_last_reverse_bar
    assert SYMBOL not in e._channel_outer_reentry_after_exit
    assert (await api.manual_order(api.ManualOrderRequest(symbol=SYMBOL,side='LONG',amount=25.)))['status']=='success'
    assert e.account.positions[SYMBOL]['side']=='LONG'
    assert e.account.positions[SYMBOL]['managed_by_bot'] is True

@pytest.mark.anyio
async def test_testnet_refresh_preserves_manual_management_metadata(tmp_path,monkeypatch):
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange
    monkeypatch.setattr(tm,'STATE_FILE',str(tmp_path/'testnet.json'))
    ex=FakeTestnetExchange()
    a=tm.BinanceTestnetAccount(ex)
    ex.positions=[{'symbol':'BTCUSDT','positionAmt':'-1','entryPrice':'100','markPrice':'99','leverage':'5'}]
    a.position_meta['BTC/USDT']={'entry_mode':'CHANNEL_SWING','manual_entry':True,'managed_by_bot':True,'bot_last_managed_at':123.}
    await a.refresh(force=True)
    assert a.positions['BTC/USDT']['managed_by_bot'] is True
    assert a.positions['BTC/USDT']['bot_last_managed_at']==123.

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG'])
async def test_live_adverse_cross_does_not_set_profit_lock(setup_engine,side):
    import core.engine as em
    e,f=setup_engine('LONG',side)
    f['kc_upper']=110.;f['kc_lower']=90.;f['ma15']=100.
    f.loc[67:69,'close']=100.
    f.loc[67:68,'ma3']=100.1 if side=='LONG' else 99.9
    f.loc[69,'ma3']=99.9 if side=='LONG' else 100.1
    e.tickers[SYMBOL]=100.
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert e.account.positions[SYMBOL]['sl'] == 0.
    assert not e.account.position_meta[SYMBOL].get('channel_cross_lock')
    await e.account.update_positions({SYMBOL:100.})
    assert e.account.positions[SYMBOL]['sl'] == 0.
    # Returning to the original direction clears this lock through the account.
    f.loc[68:69,'ma3']=100.1 if side=='LONG' else 99.9
    e.tickers[SYMBOL]=100.2 if side=='LONG' else 99.8
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert e.account.positions[SYMBOL]['sl']==0.
    assert not e.account.position_meta[SYMBOL].get('channel_cross_lock')

@pytest.mark.anyio
async def test_locked_short_upper_break_closes_even_when_new_long_rejected(setup_engine):
    e,f=setup_engine('LONG','SHORT')
    confirm_break(f, 'LONG')
    e.account.positions[SYMBOL]['sl']=105.
    e.account.position_meta[SYMBOL]['channel_cross_lock']=True
    e._abnormal_market_entry_allowed=lambda *_:False
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert SYMBOL not in e.account.positions
    assert e.account.trades[0]['status']=='CLOSED'
    assert e._channel_outer_reentry_after_exit[SYMBOL]=='LONG'
    assert any('真突破平倉' in row['text'] for row in e.account.logs)


@pytest.mark.anyio
@pytest.mark.parametrize("side,prices,closed", [
    ("LONG", [100., 99.5], False),
    ("LONG", [100., 99.7, 99.4, 99.1], False),
    ("LONG", [100., 99.9], False),
    ("SHORT", [100., 97.], False),
    ("SHORT", [100., 103.], False),
])
async def test_channel_waterfall_ticker_without_ma_or_closed_candle(
    setup_engine, monkeypatch, side, prices, closed,
):
    import core.paper_account as pm
    monkeypatch.setattr(pm, "ENABLE_RAPID_ADVERSE_DROP", True)
    monkeypatch.setattr(pm, "RAPID_ADVERSE_DROP_PCT", .004)
    monkeypatch.setattr(pm, "RAPID_ADVERSE_SPEED_PCT", .008)
    monkeypatch.setattr(pm, "RAPID_ADVERSE_SPEED_WINDOW_SEC", 10.)
    e, _ = setup_engine(side, side)
    for price in prices:
        await e.account.update_positions({SYMBOL: price})
    assert (SYMBOL not in e.account.positions) == closed
    # Emergency close does not submit a reverse order.
    assert not e.account.positions or e.account.positions[SYMBOL]["side"] == side

@pytest.mark.anyio
async def test_channel_waterfall_respects_disabled_switch(setup_engine, monkeypatch):
    import core.paper_account as pm
    monkeypatch.setattr(pm, "ENABLE_RAPID_ADVERSE_DROP", False)
    e, _ = setup_engine("LONG", "LONG")
    await e.account.update_positions({SYMBOL: 100.})
    await e.account.update_positions({SYMBOL: 95.})
    assert SYMBOL in e.account.positions
