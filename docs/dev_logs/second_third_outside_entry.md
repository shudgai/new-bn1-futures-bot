# Second/third outside entry

Latest explicit user override: after a first breakout, second and third candles should enter if not doji and the current quote stays outside the same KC rail. This supersedes full strict-gate enforcement for that two-candle window only.

Added KC_SECOND_THIRD_LONG/SHORT using a confirmed closed body breakout (first open inside/touching its KC channel and same-direction close strictly outside). No hindsight inference about intrabar tick order. The next live candle is second; the following live candle is third, provided second closed outside too. Both sides require latest quote outside and current live body ratio >20%; exactly20% is doji under the existing is_doji_candle convention. They bypass 0.5 ATR, 3 ATR distance, MA direction/spacing, channel convergence and net-room restrictions. Original strict policy remains for other entry paths; a doji cannot fall back into that path.

Signal identity includes first breakout and current live timestamp. Engine whitelist, scan and final account revalidation share the new decision. Existing finality/age, abnormal market, balance, position/slot, and duplicate-fill checks remain. Policy and candle number are stored in existing entry snapshot evidence. Profit and hard-stop/pivot-exit changes remain unchanged.

Validation:223 passed across new entry, strict gate, real engine/account revalidation with mocked submission, metadata/margin and existing exit regressions. No real orders from tests. Tests cover long/short second/third, doji and quote retreat at final boundary, fill identity deduplication. A historical breakout qualifying this exact closed-candle pattern may be recognized while its second/third candle is live; fourth candle does not use this override.
