import pytest
from tests.test_mature_reversal_exit import get_position, get_lobster_bars, run_sequence
pos = get_position("LONG")
bars = get_lobster_bars()
res = run_sequence(pos, bars)
print(res)
import json
print(json.dumps(pos['peak_trailing_state']['closed_history'], indent=2))
