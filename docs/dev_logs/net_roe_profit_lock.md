# UI Net ROE staged profit protection

The latest user authorization sets the first activation at 5%, keeps each stage four percentage points apart, and sets the peak giveback to 1.5 percentage points. The basis is the chart's displayed Net ROE%: estimated unrealized PnL minus entry/exit fees and estimated closing slippage, divided by the position margin and multiplied by 100. This is margin ROE (including leverage), not unleveraged price return.

Track the highest observed Net ROE% from live quotes only. At a peak of +5%, arm a +3.5% floor; at +9%, floor +7.5%; at +13%, floor +11.5%; and at each later four-point stage, move the floor up by four points (for example, +17% peak gives a +15.5% floor). Close at or below the active floor. Peak, floor, arming, and close-retry state are persisted under the position identity. Missing/nonpositive margin cannot arm the lock. No historical OHLC peak is reconstructed.

Existing hard and separately authorized emergency exits remain active. The existing KC outer-band and shadow-support/resistance holds still veto soft exits, including giveback, while their hold conditions apply.

Focused verification covers all four tier boundaries, long/short symmetry, fee/slippage matching the UI formula, stale quotes, missing margin, persisted arming, pending retry, and the Channel Swing realtime close whitelist.
