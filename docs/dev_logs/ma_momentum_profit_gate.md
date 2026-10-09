# MA Momentum and Profit Reentry Gate

All entry paths, including impulse and second/third candle entry, reject an adverse live MA5 slope. The forming SMA5 is corrected by the latest quote delta divided by five. Invalid MA data fail closed. Account entry revalidation applies the same contract.

CAP audit found a profitable THREE_POINT_PIVOT close without profit-exit fields followed by same-side reentry within 240 seconds. Profit evidence now uses actual close net PnL rather than an optional cached position field. Older profitable pivot closes lacking peak evidence enforce cooldown and fail closed during the remaining structural window. Historical prices are never fabricated. Existing valid events retain the close-above-peak / close-below-trough gate and previously authorized reset rules.
