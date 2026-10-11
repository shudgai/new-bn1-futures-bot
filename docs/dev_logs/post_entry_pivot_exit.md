# Post-entry pivot exit correction

## Problem
SUI and lobster accepted a pivot on the entry candle even when its start preceded the actual fill. They also searched older candles and replayed that pivot after newer candles or restart. Recent paper closes included three-point-pivot exits around the existing 120-second minimum hold boundary. This establishes a stale-signal mechanism, not proof that every early close was erroneous.

## Change
All symbols now require the pivot candle to begin strictly after the actual position opening timestamp, and its confirmation candle to be the latest closed candle. Entry-candle extrema cannot establish their ordering relative to the fill from OHLC alone. Removed the symbol-specific historical replay exception. Existing minimum-hold modification, account hard stops, waterfall exits, direction correction, and retries of already authorized pending closes remain unchanged.

## Verification
83 passed: test_kc_outer_pivot_strategy.py, test_channel_exit_grace.py, test_realtime_profit_exit.py. Includes long/short SUI/lobster rejection before and after confirmation, later candles, restart-like recovery, and minimum-hold expiry. Previous tests expecting the retired entry-candle exception were updated to reflect the requested behavior.

WAIT candidate remains isolated and disabled. No manual trading orders were placed by this repair. Live market behavior requires subsequent fills for observation.
