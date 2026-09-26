"""Momentum signals reach the real execution/account route with an offline exchange."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_service import check_entry_signals, supported_entry_reason
from test_testnet_account import FakeTestnetExchange


def candles(side, continuation):
    rows = [dict(timestamp=(i+1)*60000, open=99., close=99., high=99.5, low=98.5,
                 kc_upper=100., kc_lower=96., kc_middle=98., ma3=99., ma15=98.,
                 atr=2., volume=100., is_closed=i<3) for i in range(4)]
    if continuation:
        rows[1].update(open=100., close=100.5, high=100.7, low=99.9)
    rows[2].update(open=100., close=101., high=101.2, low=99.8)
    rows[3].update(open=101., close=101.1, high=101.2, low=100.9)
    frame = pd.DataFrame(rows)
    if side == 'SHORT':
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle'),
                    ('ma3','ma3'),('ma15','ma15')]:
            frame[a] = 200-pd.DataFrame(rows)[b]
    frame.attrs['timeframe_ms'] = 60000
    return frame


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('continuation', [False,True])
@pytest.mark.parametrize('block', [None,'daily_halt','stale_snapshot','wrong_side','duplicate'])
def test_momentum_to_market_and_persisted_atr(side, continuation, block, tmp_path, monkeypatch):
    import core.testnet_account as tm
    monkeypatch.setattr(tm, 'STATE_FILE', str(tmp_path/'account.json'))
    monkeypatch.setattr(tm, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(tm, 'notify_email', lambda *a,**k:None)
    monkeypatch.setattr(tm, 'ENABLE_EXCHANGE_INITIAL_STOP_LOSS', True)
    monkeypatch.setattr(tm, 'DISABLE_TAKE_PROFIT', True)
    monkeypatch.setattr(tm.BinanceTestnetAccount, 'credentials_configured', staticmethod(lambda:True))
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', ['DOGE/USDT'])

    async def run():
        exchange = FakeTestnetExchange()
        account = tm.BinanceTestnetAccount(exchange)
        await account.initialize()
        frame = candles(side, continuation)
        result = check_entry_signals(frame, side, 0)
        expected = ('ENTER_CONTINUATION_' if continuation else 'ENTER_FIRST_BREAKOUT_')+side
        assert result['action'] == 'ENTER' and result['reason'] == expected
        engine = object.__new__(TradingEngine)
        engine.account = account
        engine.symbol_rotation = SimpleNamespace(get_stop_cooldown_remaining=lambda *a:0., get_dynamic_leverage=lambda *a:1)
        engine.btc_1h_st_direction = 0
        engine._same_side_entry_allowed = lambda *a:True
        engine._ck_reverse_order_authorized = lambda *a:False
        engine._execution_price_is_safe = AsyncMock(return_value=True)
        engine.fetch_klines = AsyncMock(return_value=frame)
        engine.strategy = SimpleNamespace(compute_indicators=lambda f:f)
        engine.tickers = {'DOGE/USDT':float(frame.iloc[-1]['close'])}
        if block == 'stale_snapshot':
            frame.loc[2,'close'] = 99. if side=='LONG' else 101.
        if block == 'duplicate':
            engine._channel_used_confirmation = {'DOGE/USDT':(side,240000)}
        order_side = ('SHORT' if side=='LONG' else 'LONG') if block=='wrong_side' else side
        opened = await engine._execute_confirmed_channel_break(
            'DOGE/USDT', frame, engine.tickers['DOGE/USDT'], order_side,
            daily_halt=block=='daily_halt', v8_reason=expected)
        if block:
            assert not opened and not exchange.orders and not account.positions
            return
        assert opened
        assert exchange.orders[0]['type'] == 'market'
        assert exchange.orders[0]['side'] == ('buy' if side=='LONG' else 'sell')
        p = account.positions['DOGE/USDT']
        sign = 1 if side=='LONG' else -1
        assert p['entry_atr'] == 2.
        assert p['atr_sl'] == pytest.approx(p['entry_price']-sign*3.)
        stop = next(o for o in exchange.orders if o['type']=='STOP_MARKET')
        assert float(stop['params']['triggerPrice']) == pytest.approx(p['atr_sl'])
        assert account.position_meta['DOGE/USDT']['entry_snapshot']['signal_code'] == expected
        saved = json.loads((tmp_path/'account.json').read_text())
        assert saved['position_meta']['DOGE/USDT']['entry_atr'] == 2.
        assert any(t['action']=='OPEN_'+side for t in account.trades)
    asyncio.run(run())


@pytest.mark.parametrize('code,side', [('MA_CROSS_GOLDEN_LONG','LONG'),('MA_CROSS_DEATH_SHORT','SHORT')])
def test_legacy_codes_remain_supported(code,side):
    assert supported_entry_reason(code,side)
    assert not supported_entry_reason(code,'SHORT' if side=='LONG' else 'LONG')


@pytest.mark.parametrize('code', [None,'ENTER_UNKNOWN_LONG','ENTER_FIRST_BREAKOUT_LONG_extra',{},''])
def test_unknown_codes_remain_blocked(code):
    assert not supported_entry_reason(code,'LONG')
