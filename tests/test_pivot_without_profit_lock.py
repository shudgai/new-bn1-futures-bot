import copy
import pytest
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, evaluate_peak_trailing, migrate_peak_state,
)
from test_kc_outer_pivot_strategy import position,snapshot


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('symbol',['SUI/USDT','龙虾/USDT','DOGE/USDT'])
def test_confirmed_post_entry_pivot_does_not_close_on_pullback(side,symbol):
    p=position(side,symbol)
    p['margin']=100.
    result=evaluate_peak_trailing(p,101. if side=='LONG' else 99.,snapshot(side),fee=0.,slippage=0.)
    assert result is None
    assert evaluate_peak_trailing(p,100.,{'quote_ms':360000.},fee=0.,slippage=0.) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_net_roe_lock_and_saved_pending_are_cleared(side):
    p=position(side,'龙虾/USDT')
    p['margin']=10.
    migrate_peak_state(p)
    p[STATE_KEY].update(net_roe_peak_pct=6.,net_roe_lock_armed=True,
                        net_roe_lock_floor_pct=4.,pending='EXIT_REALTIME_PEAK_TRAILING',
                        trigger='NET_ROE_STAGED_GIVEBACK')
    meta={STATE_KEY:copy.deepcopy(p[STATE_KEY])}
    migrate_peak_state(p,meta)
    assert not p[STATE_KEY].get('pending')
    assert not meta[STATE_KEY].get('net_roe_lock_armed')
    sign=1 if side=='LONG' else -1
    result=evaluate_peak_trailing(p,100+sign*.3,{'quote_ms':300000.},fee=0.,slippage=0.)
    assert result is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_entry_candle_pivot_is_still_rejected(side):
    p=position(side,'龙虾/USDT')
    p['open_timestamp']=195.
    assert evaluate_peak_trailing(p,101.,snapshot(side,quote_ms=330000.),fee=0.,slippage=0.) is None
