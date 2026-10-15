import pytest

import core.paper_account as pa_module
from core.paper_account import (
    PaperAccount,
    get_outer_run_net_giveback_usdt,
    get_profit_lock_giveback_usdt,
)


def test_profit_lock_giveback_scales_with_peak_ratio():
    # Below the ratio break-even point, the fixed 1.0U floor applies.
    assert get_profit_lock_giveback_usdt(0.0) == pytest.approx(1.0)
    assert get_profit_lock_giveback_usdt(1.0) == pytest.approx(1.0)
    # Above it, PROFIT_LOCK_TRAIL_RATIO (0.20) takes over so bigger peaks
    # tolerate a proportionally wider giveback instead of a fixed 1.0U.
    assert get_profit_lock_giveback_usdt(5.0) == pytest.approx(1.0)
    assert get_profit_lock_giveback_usdt(100.0) == pytest.approx(20.0)


def test_outer_run_net_giveback_is_fixed_for_every_position_size():
    assert get_outer_run_net_giveback_usdt(75.0) == pytest.approx(1.0)
    assert get_outer_run_net_giveback_usdt(150.0) == pytest.approx(1.0)
    assert get_outer_run_net_giveback_usdt(300.0) == pytest.approx(1.0)










