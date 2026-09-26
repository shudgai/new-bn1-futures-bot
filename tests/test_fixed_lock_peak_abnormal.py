import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits.fixed_profit_lock import fixed_profit_lock, enforce_fixed_profit_lock
from core.services.exits.peak_abnormal_exit import peak_abnormal_exit
from core.services.exits.profit_protection_service import ProfitProtectionExitStrategy


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def position(side):
    return dict(side=side, entry_price=100., qty=1., open_timestamp=60., entry_mode='CHANNEL_SWING')


def quote_for_net(net, side, fee=.0005, slip=.0001):
    sign = 1 if side == 'LONG' else -1
    return (net + (sign + fee)*100.) / ((sign-fee)*(1-sign*slip))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_exact_fixed_ladder_and_floor_only_rises(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    for net, floor in [(3.99,0), (4.,2.), (5.99,2.), (6.,4.), (8.,6.), (7.,6.)]:
        r = fixed_profit_lock(p, 100+sign*net, 0., 0.)
        assert r['locked_net'] == floor
        assert not r['triggered']
    restored = json.loads(json.dumps(p))
    r = fixed_profit_lock(restored, 100+sign*6., 0., 0.)
    assert r['triggered'] and r['reason'] == 'FIXED_NET_PROFIT_LOCK_EXIT'
    assert fixed_profit_lock(restored, 100+sign*10., 0., 0.)['triggered']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_fee_slippage_aware_floor_price(side):
    p=position(side)
    r=fixed_profit_lock(p,quote_for_net(6.1,side),.0005,.0001)
    assert r['locked_net'] == 4.
    assert r['stop_price'] == pytest.approx(quote_for_net(4.,side))
    assert not r['triggered']
    assert fixed_profit_lock(p,quote_for_net(3.99,side),.0005,.0001)['triggered']


def candles():
    return pd.DataFrame([dict(timestamp=t, open=100., close=100., atr=2., is_closed=t<120000)
                         for t in (0,60000,120000)])


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_abnormal_requires_observed_peak_no_atr_turn_threshold(side):
    p=position(side); f=candles(); sign=1 if side=='LONG' else -1
    assert peak_abnormal_exit(p,f,100.) is None
    # Abnormal candle without a post-entry favorable turn does not exit.
    assert peak_abnormal_exit(p,f,100-sign*1.1) is None
    assert peak_abnormal_exit(p,f,100+sign*.01) is None
    # Tiny actual reversal confirms the observed peak: no 0.10 ATR gate.
    assert peak_abnormal_exit(p,f,100+sign*.009) is None
    assert p['channel_peak_abnormal']['confirmed']
    assert peak_abnormal_exit(p,f,100-sign*.99) is None
    assert peak_abnormal_exit(p,f,100-sign*1.01) == 'PEAK_CONFIRMED_ADVERSE_ABNORMAL_EXIT'
    assert peak_abnormal_exit(json.loads(json.dumps(p)),None,100.) == 'PEAK_CONFIRMED_ADVERSE_ABNORMAL_EXIT'


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_new_position_does_not_inherit_old_peak(side):
    p=position(side); f=candles(); sign=1 if side=='LONG' else -1
    for price in [100,100+sign,100-sign*1.1]:
        peak_abnormal_exit(p,f,price)
    assert p['channel_peak_abnormal']['pending']
    p['open_timestamp']=120.
    assert peak_abnormal_exit(p,f,100-sign*1.1) is None
    assert not p['channel_peak_abnormal']['confirmed']


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_ma_exit_is_independent_of_lock_and_price_retreat(side):
    p=position(side)
    p['channel_profit_protection']={'armed':True, 'locked_net':2.}
    # Existing MA3/MA15 exit stays active even when the fixed floor is armed.
    sign=1 if side=='LONG' else -1
    f=pd.DataFrame([dict(timestamp=(i+1)*60000, close=100., open=100., atr=2., is_closed=i<15)
                    for i in range(16)])
    strategy=ProfitProtectionExitStrategy()
    strategy.evaluate_exit(p,f,100+sign)
    assert strategy.evaluate_exit(p,f,100-sign*.001) == 'EXIT_TRUE_TOP_STRUCTURE_BREAK_MA3'


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
async def test_fixed_exit_persists_before_order_and_retries(side):
    p=position(side); sign=1 if side=='LONG' else -1
    account=SimpleNamespace(positions={'X':p},position_meta={},save_state=Mock(),close_position=AsyncMock(return_value=False))
    assert not await enforce_fixed_profit_lock(account,'X',100+sign*6.,0.,0.)
    assert await enforce_fixed_profit_lock(account,'X',100+sign*4.,0.,0.)
    assert account.position_meta['X']['channel_profit_protection']['pending']
    assert account.close_position.call_args.args[1] == 100+sign*4.
    assert await enforce_fixed_profit_lock(account,'X',100+sign*8.,0.,0.)
    assert account.close_position.await_count == 2
