# Live third-candle entry

The first body must cross its KC outer rail and the second completed candle must confirm outside with the existing closed MA5/MA15 alignment and MA5 direction. Entry is evaluated during the immediately following third candle, without waiting for its close. Fourth-candle recovery is disabled.

The third candle may have a same-direction body of any positive size. An opposite body is allowed only when its absolute body is at most 25% of the observed high-low range, including the current quote. This inclusive threshold was explicitly selected by the user. A flat candle with a nonzero range qualifies as a zero-body weak candle; a zero-range opening tick waits. Observed wicks define range only, never historical tick order. Live MA confirmation is not required; the completed pair supplies MA structure.

The latest quote must remain strictly outside its live rail. The existing third-candle maximum distance of 2 ATR is retained, with the latest completed ATR as scale. Original breakout identity, successful-fill deduplication, same-close-bar prohibition and account risk checks remain. Exits are unchanged. Fresh account validation replaces the cached-snapshot authorization shortcut. Existing settlement checks verify the completed pair and can delay entry seconds within the third candle.

Validation: 28 focused tests cover both sides, 25% inclusion, just-above rejection, latest-quote reversal/recovery, closed-third rejection, scan/account agreement, account snapshot revalidation, and isolated real-engine paper fills/deduplication. No real test orders. Old closed-third/fourth-entry tests represent superseded policy; no whole-repository pass is claimed. Referenced AIDAN specification files are absent.

Concurrent edits in core/services/symbol_runner.py and pre-existing untracked files were left untouched.
