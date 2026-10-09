import sys
sys.path.append('.')
from tests.test_mature_reversal_exit import get_position, get_lobster_bars, run_sequence
pos = get_position("LONG")
bars = get_lobster_bars()
res = run_sequence(pos, bars)
print(res)
print(pos)
