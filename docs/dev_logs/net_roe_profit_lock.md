# UI Net ROE staged profit protection

## Current position protection update

The current authorization supersedes the earlier 5% staged-lock proposal. The active lock starts at 8% net ROE, uses 5-point tier intervals, and continuously raises a monotonic stop to peak net ROE minus 3 percentage points. Net PnL and ROE use the chart's verified fee/slippage estimate, funding cost, and the position's initial margin.

Two consecutive adverse closed 1-minute candles may independently exit a profitable position when the second close progresses against the position and breaks MA5 or the prior short-term structure. A single adverse candle qualifies only at 0.5 ATR or larger and a confirmed structure break. Unclosed candles do not count. The trigger is latched in SQLite state keyed by a deterministic position UID; updates use a revision check and all exits still go through the existing priority close authority.

Ordinary per-position percentage, ATR, and ROE hard stops are disabled. Account-level emergencies, verified waterfall exits, liquidation/data-integrity protections, and the existing Phase 3C close path remain separate.

Verification: the final focused protection, priority-exit, hard-stop, Channel Swing, and pivot run passed 158 tests. A separate legacy close-authority module has two unrelated failures because its `DataPipeline` fixture returns `None`; that module was not changed.

## Earlier 5% staged-lock proposal

The prior proposal used a 5% start, four-point stages, and a 1.5-point drawdown. It was superseded by the current 8% / 5-point / 3-point policy above.
