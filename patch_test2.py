import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
import core.services.exits.peak_trailing_exit as pt
old_eval = pt.evaluate_peak_trailing
def my_eval(*args, **kwargs):
    res = old_eval(*args, **kwargs)
    print("my_eval returning:", res)
    return res
pt.evaluate_peak_trailing = my_eval
pytest.main(['tests/test_peak_trailing_policy.py::test_waterfall_priority_over_soft_exits', '-v', '--tb=short', '-s'])
