import pytest
import core.services.exits.peak_trailing_exit as pt
old_eval = pt.evaluate_peak_trailing
def my_eval(position, price, snapshot, atr, *args, **kwargs):
    res = old_eval(position, price, snapshot, atr, *args, **kwargs)
    print("EVAL RESULT:", res)
    return res
pt.evaluate_peak_trailing = my_eval
pytest.main(['tests/test_realtime_profit_exit.py::test_real_account_reload_and_concurrent_tick_close', '-v', '--tb=short', '-s'])
