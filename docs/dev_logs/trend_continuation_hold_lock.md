# Trend Continuation Hold Lock

Every non-hard Channel Swing exit is vetoed if any observed directional candle, non-adverse MA5 slope, or same-side MA5/KC rail support remains. LONG and SHORT are symmetric. Realtime CK correction and automatic breakout reversal apply the same arbitration. Explicit manual closes remain available.

Profit locks additionally require an unleveraged historical price peak of at least 5%, at least 1.2 percentage points of giveback, and price beyond MA5 in the adverse direction with an adverse MA5 slope. Existing wider tier allowances remain. Pending closes are revalidated and cancelled if conditions recover; missing MA confirmation cannot authorize profit-taking.

This user override also blocks non-hard emergency exits while any continuation condition holds. Hard-stop handling remains independent, using existing enabled policies.
