"""Production calls with isolated accounts/exchanges; never read or write live state."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core import config
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from core.testnet_account import BinanceTestnetAccount
from core.symbol_aliases import is_meme_breakout_symbol

PEPE = '1000PEPE/USDT'
LOBSTER = '龙虾/USDT'


def held(side='LONG'):
    sign=1 if side=='LONG' else -1
    return dict(side=side,entry_price=100.,qty=1.,amount=100.,margin=100.,leverage=1.,
                open_timestamp=time.time()-180,entry_mode='CHANNEL_SWING',entry_atr=1.,
                initial_sl=100-sign*1.5,sl=100-sign*1.5,tp=0.,unrealized_pnl=0.)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self:None)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)
    monkeypatch.setattr('core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',lambda **kwargs:None)
    monkeypatch.setattr('core.services.reversal_shadow_logger.record_reversal_shadow_candidates',lambda *args:None)


def account_for(kind, state_file=None):
    if kind=='paper':
        a=PaperAccount();a.balance=1000.
    else:
        exchange=SimpleNamespace(create_order=AsyncMock(return_value={'id':'mock','status':'closed'}),
            request=AsyncMock(return_value={'algoId':'mock-stop'}),cancel_order=AsyncMock(),
            fetch_order=AsyncMock(return_value={'status':'canceled','filled':0.}),
            amount_to_precision=lambda symbol,q:str(q),price_to_precision=lambda symbol,p:str(p))
        a=BinanceTestnetAccount(exchange, state_file=state_file)
        a.refresh=AsyncMock(return_value=1000.)
    a.log=Mock()
    return a


def test_active_board_uses_shared_wild_swing_symbols():
    assert config.DEFAULT_SYMBOLS == [LOBSTER, PEPE]
    assert not config.is_entry_disabled(LOBSTER)
    assert not config.is_entry_disabled(PEPE)
    assert is_meme_breakout_symbol(LOBSTER)
    assert is_meme_breakout_symbol(PEPE)


def test_fixed_entry_scan_includes_both_symbols():
    symbols = TradingEngine._entry_scan_symbol_snapshot(
        config.DEFAULT_SYMBOLS,
        config.DEFAULT_SYMBOLS,
        {},
        {},
        True,
        2,
    )

    assert symbols == [LOBSTER, PEPE]


def test_cancel_race_partial_fill_is_still_managed(tmp_path):
    a=account_for('testnet', str(tmp_path/'testnet.json'));a.positions[PEPE]=held()
    a.pending_limit_orders[PEPE]=dict(order_id='pending-entry',side='LONG',qty=1.,target_price=100.,
        sl=98.5,tp=0.,reason='old-entry',atr=1.,leverage=1,signal_score=100,amount_usdt=100.)
    a.exchange.fetch_order.return_value={'status':'canceled','filled':.2,'average':100.}
    a._finalize_new_position=AsyncMock()
    asyncio.run(a.check_pending_limit_orders())
    a._finalize_new_position.assert_awaited_once()
    assert a._finalize_new_position.await_args.args[3]==.2
    a._raw_create_order.assert_not_awaited()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_existing_paper_position_valuation_sl_and_hard_stop_execute(side):
    a=account_for('paper');a.positions[PEPE]=held(side);a.close_position=AsyncMock(return_value=True)
    sign=1 if side=='LONG' else -1
    asyncio.run(a.update_positions({PEPE:100+sign*.1}))
    assert a.positions[PEPE]['mark_price']==100+sign*.1
    a.close_position.assert_not_awaited()
    assert asyncio.run(a.trail_stop_loss(PEPE,100-sign*1.4,False))
    asyncio.run(a.update_positions({PEPE:100-sign*10.}))
    a.close_position.assert_awaited_once()
    assert 'HARD_STOP' in a.close_position.await_args.args[2]


def test_existing_testnet_update_and_native_sl_still_execute(tmp_path):
    a=account_for('testnet', str(tmp_path/'testnet.json'));a.positions[PEPE]=held()
    asyncio.run(a.update_positions({PEPE:100.1}))
    a.refresh.assert_awaited_once() # CHANNEL_SWING is valued by refresh; ticker exits are intentionally skipped.
    asyncio.run(a._create_protection_order(PEPE,'sell','STOP_MARKET',1.,98.5))
    assert a.exchange.request.await_args.args[3]['reduceOnly']=='true'


def test_existing_pepe_is_in_the_active_fixed_scan():
    symbols = TradingEngine._entry_scan_symbol_snapshot(
        config.DEFAULT_SYMBOLS,
        config.DEFAULT_SYMBOLS,
        {},
        {},
        True,
        2,
    )
    assert symbols == [LOBSTER, PEPE]


def test_cancel_failure_keeps_order_tracked_and_does_not_submit(monkeypatch,tmp_path):
    monkeypatch.setattr(config,'ENTRY_DISABLED_SYMBOLS',{PEPE})
    a=account_for('testnet', str(tmp_path/'testnet.json'));a.positions[PEPE]=held()
    a.pending_limit_orders[PEPE]=dict(order_id='pending-entry',side='LONG',qty=1.,target_price=100.)
    a.exchange.cancel_order.side_effect=RuntimeError('simulated exchange timeout')
    asyncio.run(a.check_pending_limit_orders())
    assert PEPE in a.pending_limit_orders
    assert PEPE in a.positions
    a._raw_create_order.assert_not_awaited()


def test_environment_can_enable_pepe_when_not_disabled():
    import os,subprocess,sys,json
    env=dict(os.environ,ENTRY_DISABLED_SYMBOLS='',DEFAULT_SYMBOLS=PEPE+','+LOBSTER)
    result=subprocess.check_output([sys.executable,'-c',
        'import json; from core.config import DEFAULT_SYMBOLS,is_entry_disabled; '
        'print(json.dumps([DEFAULT_SYMBOLS,is_entry_disabled("1000PEPEUSDT")]))'],env=env,text=True)
    assert json.loads(result.strip())==[[PEPE,LOBSTER],False]
