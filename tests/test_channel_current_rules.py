"""Regression cases for the user-approved 2026-09-09 Channel Swing rules."""
import pytest
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from core.testnet_account import BinanceTestnetAccount
from test_channel_swing import _generate_macro_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('invalid', ['wick', 'already_outside', 'gap', 'live_only'])
def test_new_break_requires_closed_body_from_inside(side, invalid):
    f = _generate_macro_frame('UP' if side == 'LONG' else 'DOWN', 70)
    # Avoid a pullback candidate so this test isolates the breakout route.
    f['ma3'] = f['ma15']
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    edge = 108.0 if side == 'LONG' else 92.0
    sign = 1 if side == 'LONG' else -1
    f[rail] = edge
    f.loc[67, 'close'] = edge - sign * .5
    f.loc[68, ['open', 'close', 'ma3', 'ma15']] = [edge-sign*.5, edge+sign*.5, edge+sign*.2, edge-sign*.1]
    if invalid == 'wick': f.loc[68, 'close'] = edge-sign*.1
    if invalid == 'already_outside': f.loc[67, ['open', 'close']] = edge+sign*.1
    if invalid == 'gap': f.loc[68, 'open'] = edge+sign*.1
    if invalid == 'live_only':
        f.loc[68, 'close'] = edge-sign*.1
        f.loc[69, 'close'] = edge+sign*.5
    result = TradingEngine._channel_swing_action(f, edge+sign*.5)
    assert result['action'] == 'WAIT', result


def _confirm_outer_break(f, side):
    f['kc_upper'], f['kc_lower'] = 102., 98.
    f['open'] = f['close'] = 100.
    f['ma15'] = [100. + (i*.01 if side == 'LONG' else -i*.01) for i in range(len(f))]
    f.loc[f.index[-3], ['open', 'close']] = [100., 103.] if side == 'LONG' else [100., 97.]
    f.loc[f.index[-2], ['open', 'close']] = [103., 103.2] if side == 'LONG' else [97., 96.8]
    f.loc[f.index[-1], ['open', 'close']] = 103.2 if side == 'LONG' else 96.8
    f['high'] = f[['open', 'close']].max(axis=1) + .1
    f['low'] = f[['open', 'close']].min(axis=1) - .1


def test_thirty_bars_can_confirm_outer_break():
    f = _generate_macro_frame('DOWN', 30)
    _confirm_outer_break(f, 'SHORT')
    assert TradingEngine._channel_swing_action(f, 96.8)['side'] == 'SHORT'

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_paper_lock_is_monotonic_and_unlock_restores_fixed_sl(tmp_path, monkeypatch, side):
    import core.paper_account as module
    monkeypatch.setattr(module, 'STATE_FILE', str(tmp_path/'paper.json'))
    a = PaperAccount()
    fixed, lock, worse = (90., 109., 108.) if side == 'LONG' else (110., 91., 92.)
    a.positions[SYMBOL] = {'side': side, 'entry_price': 100., 'sl': fixed, 'tp': 0.}
    a.position_meta[SYMBOL] = {'channel_cross_lock': True, 'channel_pre_lock_sl': fixed}
    assert await a.trail_stop_loss(SYMBOL, lock)
    assert not await a.trail_stop_loss(SYMBOL, worse)
    assert a.positions[SYMBOL]['sl'] == lock
    assert await a.clear_channel_profit_lock(SYMBOL)
    assert a.positions[SYMBOL]['sl'] == fixed
    assert not await a.clear_channel_profit_lock(SYMBOL)

@pytest.mark.anyio
@pytest.mark.parametrize('fail', [False, True])
async def test_testnet_unlock_cancels_only_lock_and_keeps_state_on_failure(fail):
    a = object.__new__(BinanceTestnetAccount)
    a.closing_lock = set()
    a.positions = {SYMBOL: {'side':'LONG', 'qty':1., 'sl':109., 'is_breakeven_moved':True}}
    a.position_meta = {SYMBOL: {'channel_cross_lock':True, 'channel_lock_algo_id':'123', 'channel_pre_lock_sl':0.}}
    a.save_state = lambda: None
    a.log = lambda *_: None
    calls = []
    class Exchange:
        async def request(self, *args):
            calls.append(args)
            if fail: raise RuntimeError('exchange unavailable')
            return {'code':200}
    a.exchange = Exchange()
    assert await a.clear_channel_profit_lock(SYMBOL) is (not fail)
    assert calls == [('algoOrder', 'fapiPrivate', 'DELETE', {'algoId':'123'})]
    assert a.positions[SYMBOL]['sl'] == (109. if fail else 0.)
    assert bool(a.position_meta[SYMBOL].get('channel_cross_lock')) is fail

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_paper_ticker_preserves_channel_stop_but_defers_exit_to_engine(tmp_path, monkeypatch, side):
    import core.paper_account as module
    monkeypatch.setattr(module, 'STATE_FILE', str(tmp_path/'paper.json'))
    a = PaperAccount()
    stop = 99. if side == 'LONG' else 101.
    a.positions[SYMBOL] = {'side':side, 'entry_mode':'CHANNEL_SWING', 'entry_price':100., 'qty':1., 'sl':stop, 'tp':0., 'margin':10.}
    a.position_meta[SYMBOL] = {'sl':stop}
    calls=[]
    async def close(*args, **kwargs): calls.append((args, kwargs)); return True
    a.close_position = close
    await a.update_positions({SYMBOL:100.})
    assert a.positions[SYMBOL]['sl'] == stop
    assert calls == []
    await a.update_positions({SYMBOL:stop})
    assert calls == []
    assert a.positions[SYMBOL]["sl"] == stop

@pytest.mark.anyio
@pytest.mark.parametrize('old_side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('close_ok', [False, True])
async def test_reversal_closes_first_and_submits_opposite_order(old_side, close_ok, monkeypatch):
    f = _generate_macro_frame('DOWN' if old_side == 'LONG' else 'UP', 70)
    f['atr']=2.; f['volume']=2000.; f['vol_ma_20']=1000.
    _confirm_outer_break(f, 'SHORT' if old_side == 'LONG' else 'LONG')
    e = _execution_engine(f, old_side, close_ok)
    e._channel_swing_last_reverse_bar = {}
    e.tickers[SYMBOL]=float(f.loc[68,'close'])
    e.st_direction_1h_cache={}
    e.market_prebreakout_directions={}
    for name in ('_channel_inner_trend_hold', '_channel_profit_wait_candidates',
                 '_channel_swing_last_exit_at', '_channel_swing_last_exit_side',
                 '_channel_emergency_reentry_wait', '_channel_swing_peak_exit_info'):
        setattr(e, name, {})
    e._touch_entry_math_favorable=lambda *_: True
    e._channel_max_net_loss_action=lambda *_: {'action':'HOLD'}
    submitted = []
    async def place(symbol, signal, price):
        submitted.append(signal['side'])
        return True
    e._place_structured_entry = place
    _, candidates = await e._process_single_symbol(SYMBOL, 1., None, False)
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][0] == 'close'
    assert candidates == []
    assert submitted == (["SHORT" if old_side == "LONG" else "LONG"] if close_ok else [])
    if close_ok:
        assert SYMBOL not in e._channel_outer_reentry_after_exit

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('outside', [False, True])
async def test_stale_reverse_cannot_retry_without_closed_break(side, outside):
    f = _generate_macro_frame('UP' if side == 'LONG' else 'DOWN', 70)
    f['atr']=2.; f['volume']=2000.; f['vol_ma_20']=1000.
    f['ma3']=f['ma15']
    f['kc_upper']=110.; f['kc_lower']=90.
    f['open']=100.; f['close']=100.
    e = _execution_engine(f, 'SHORT' if side=='LONG' else 'LONG', True)
    e.account.positions.clear()
    e.market_prebreakout_directions={}
    e.st_direction_1h_cache={}
    for name in ('_channel_inner_trend_hold', '_channel_profit_wait_candidates',
                 '_channel_swing_last_exit_at', '_channel_swing_last_exit_side',
                 '_channel_emergency_reentry_wait', '_channel_swing_peak_exit_info'):
        setattr(e, name, {})
    e._channel_outer_reentry_after_exit[SYMBOL]=side
    e.tickers[SYMBOL]=(111. if side=='LONG' else 89.) if outside else 100.
    e._touch_entry_math_favorable=lambda *_: True
    submitted = []
    async def place(symbol, signal, price):
        submitted.append(signal['side'])
        return True
    e._place_structured_entry = place
    _, candidates=await e._process_single_symbol(SYMBOL, 1., None, False)
    assert not any('處理失敗' in msg for msg,_ in e.account.logs), e.account.logs
    assert candidates == []
    assert submitted == []
    assert SYMBOL not in e._channel_outer_reentry_after_exit

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_fresh_snapshot_rejects_inside_channel_pullback(side):
    f = _generate_macro_frame('UP' if side == 'LONG' else 'DOWN', 70)
    if side == 'LONG':
        f.loc[66:68, 'ma3'] = [107.,106.5,106.9]
        f.loc[68,['close','ma15']] = [107.,106.8]
    else:
        f.loc[66:68,'ma3'] = [93.,93.5,93.1]
        f.loc[68,['close','ma15']] = [93.,93.2]
    e = _execution_engine(f, side, True)
    e.tickers[SYMBOL] = float(f.loc[68,'close'])
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side) is None
    assert e._channel_entry_min_profit_ok('ENTER', False, side, e.tickers[SYMBOL], f)


def _short_single_abnormal_frame():
    f = _generate_macro_frame('DOWN', 70)
    f['kc_middle'] = 100.
    f['kc_upper'] = 102.
    f['kc_lower'] = 98.
    f['atr'] = 4.  # Ordinary favorable body: not a waterfall.
    f.loc[67, ['open', 'close', 'ma3', 'ma15']] = [100., 99.5, 99.4, 100.]
    f.loc[68, ['open', 'close', 'high', 'ma3', 'ma15']] = [99.5, 101., 105., 100.5, 100.]
    return f


def test_short_single_live_pump_holds_without_cross_lock():
    f = _short_single_abnormal_frame()
    # Live price and wick outside; the closed candle body has not broken out.
    result = TradingEngine._channel_swing_action(f, 105., 'SHORT')
    assert result == {'action':'HOLD', 'side':None, 'reason':'HOLDING_SHORT_RUN_TO_LOW'}


def test_short_single_pump_without_cross_keeps_position():
    f = _short_single_abnormal_frame()
    f.loc[68, 'ma3'] = 99.8
    result = TradingEngine._channel_swing_action(f, 105., 'SHORT')
    assert result['action'] == 'HOLD'
    assert result['reason'] == 'HOLDING_SHORT_RUN_TO_LOW'


def test_short_after_lock_unlocks_below_ma15_even_above_kc_middle():
    f = _short_single_abnormal_frame()
    f.loc[67, ['ma3','ma15']] = [100.5,100.]
    f.loc[68, ['ma3','ma15','close','kc_middle']] = [99.8,100.,99.9,99.5]
    result = TradingEngine._channel_swing_action(f, 99.9, 'SHORT', profit_locked=True)
    assert result['reason'] == 'HOLDING_SHORT_RUN_TO_LOW'


def test_short_locked_single_pump_then_true_upper_break_closes_first():
    f = _short_single_abnormal_frame()
    _confirm_outer_break(f, 'LONG')
    result = TradingEngine._channel_swing_action(f, 103., 'SHORT', profit_locked=True)
    assert result['action'] == 'REVERSE'
    assert result['reason'] == 'KC_UPPER_BREAKOUT'


def test_two_adverse_candles_without_favorable_run_or_structure_failure_hold():
    f = _short_single_abnormal_frame()
    f.loc[67, ['open','close']] = [99.,101.]
    f.loc[68, ['open','close']] = [101.,103.]
    result = TradingEngine._channel_swing_action(f, 103., 'SHORT', profit_locked=True)
    assert result['action'] == 'HOLD'
    assert result['reason'] == 'HOLDING_SHORT_RUN_TO_LOW'

@pytest.mark.anyio
async def test_testnet_channel_short_ticker_spike_does_not_force_close(tmp_path, monkeypatch):
    import core.testnet_account as module
    from test_testnet_account import FakeTestnetExchange
    monkeypatch.setattr(module, 'STATE_FILE', str(tmp_path/'testnet.json'))
    monkeypatch.setattr(module, 'ENABLE_RAPID_ADVERSE_DROP', True)
    a = BinanceTestnetAccount(FakeTestnetExchange())
    a.positions[SYMBOL] = {'side':'SHORT', 'entry_mode':'CHANNEL_SWING', 'entry_price':100., 'qty':1., 'sl':0., 'tp':0., 'margin':10., 'mark_price':105.}
    a.position_meta[SYMBOL] = {'entry_mode':'CHANNEL_SWING'}
    a._last_ticker_prices[SYMBOL] = 100.
    calls = []
    async def refresh(): pass
    async def close(*args, **kwargs): calls.append(args); return True
    a.refresh = refresh
    a.close_position = close
    await a.update_positions({SYMBOL:105.})
    assert calls == []
    assert SYMBOL in a.positions
    # The exception must not disable an existing protective stop.
    a.positions[SYMBOL]['sl'] = 104.
    await a.update_positions({SYMBOL:105.})
    assert calls == []
    a.positions[SYMBOL]['channel_cross_lock'] = True
    a.position_meta[SYMBOL]['channel_cross_lock'] = True
    await a.update_positions({SYMBOL:105.})
    assert calls == []
