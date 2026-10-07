# Independent confirmed pivot exit — 2026-10-07

The user reports delayed lobster short closing beyond the KC middle and requests
peak/valley exits. The actual 14:12:38 Taipei close was a structural waterfall at
0.06648665, net -3.2502 USDT. Observed minimum was 0.06292 and observed net peak
6.405 USDT. There was no profit lock. The prior early-exit evidence reused a new
opposite-entry helper requiring an outside pivot, live half-ATR body and break of
the confirming candle extreme, which can fail during an earlier valid reversal.

Add independent `EXIT_CONFIRMED_PIVOT_TURN`, with three contiguous completed
candles. A long needs a strict local high; a short needs a strict local low.
The pivot must start after actual entry, and its right neighbour must be the
latest completed candle. That neighbour must have an adverse real body and close
adversely beyond the pivot candle's close; the latest quote must remain at or
beyond this confirming close. Reject missing, invalid, premature or stale data.

Noise filters are implementation defaults: retain the existing requirement of
at least 0.5 frozen entry ATR of genuinely observed favorable movement; require
the confirming adverse body to be at least 0.1 frozen entry ATR and 20% of its
candle range. No MA alignment, middle crossing, opposite entry, external rail,
net-profit activation, percentage giveback or moving protection line is required.
This is a completed-pivot turn exit, not a predictive absolute peak/valley fill.

Preserve structure, initial/account hard stops and waterfall priority. Valid
pivot exit retries persist through restart, rebound and absent fresh data. The
realtime account close runs independently of entry REST/provider checks and logs
the pivot, confirmation, fixed ATR and quote evidence. Entry gates and automatic
netting reversal remain unchanged; profit locks remain disabled.

Saved API closed candles at 14:00/14:01/14:02 have lows
0.06365/0.06292/0.06409. The 14:02 adverse short-confirmation candle closes 0.06482,
below its KC middle of about 0.0654593. A quote of 0.06482 after that candle has
completed passes the new exit. This is closed-evidence replay, not an observed
14:03 historical quote or alternate executed fill; data before the right candle
finishes cannot authorize this exit.

246 focused regression tests passed, including both directions, noise/data/
post-entry rejection, independent real paper closing, preserved pending retries,
the recorded lobster replay, no-lock holding, structural and hard-risk exits,
entry authority, finality, proof signatures and durable direct reversals.
Compilation and diff checks passed. Authorized commit and paper-service restart
preserve account history and existing positions.
