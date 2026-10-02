import pytest, sys
from core.engine import TradingEngine
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
def my_eval(position, price, snapshot, atr, *args, **kwargs):
    print("EVAL PEAK:", position.get('__PEAK_TRAILING__', {}).get('peak_net_pnl'))
    return evaluate_peak_trailing(position, price, snapshot, atr, *args, **kwargs)
import core.services.strategies.pure_trend_v2 as pt
pt.evaluate_peak_trailing = my_eval
pytest.main(['tests/test_realtime_profit_exit.py::test_real_account_reload_and_concurrent_tick_close', '-v', '--tb=short', '-s'])
