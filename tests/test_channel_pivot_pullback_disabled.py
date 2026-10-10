import copy
import pytest
from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON, STATE_KEY, evaluate_peak_trailing, migrate_peak_state,
)
from test_kc_outer_pivot_strategy import position, snapshot


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('symbol', ['SUI/USDT','龙虾/USDT','DOGE/USDT'])
def test_old_pivot_retry_is_retired_without_closing(side,symbol):
    p=position(side,symbol)
    migrate_peak_state(p)
    p[STATE_KEY].update(pending=ABNORMAL_REASON,trigger='THREE_POINT_PIVOT',
                        trigger_bar_ms=180000.,trigger_confirmed_ms=240000.)
    meta={STATE_KEY:copy.deepcopy(p[STATE_KEY])}
    migrate_peak_state(p,meta)
    assert not p[STATE_KEY].get('pending')
    assert not meta[STATE_KEY].get('trigger')
    assert evaluate_peak_trailing(p,101.,snapshot(side),fee=0.,slippage=0.) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_unarmed_ui_return_does_not_close_on_pivot(side):
    p=position(side,'龙虾/USDT')
    p['margin']=100.
    price=101. if side=='LONG' else 99.
    assert evaluate_peak_trailing(p,price,snapshot(side),fee=0.,slippage=0.) is None
    assert not p[STATE_KEY].get('net_roe_lock_armed')
