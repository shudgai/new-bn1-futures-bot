# Same-color confirmation after a doji

A colored closed doji in either of the two entry candles no longer blocks entry when its immediate successor is a same-color non-doji body (body/range at least 10%). The successor may be the live candle, evaluated at the latest quote without waiting for closure. A zero-body doji has no color and remains blocked. Consecutive dojis cannot skip ahead to a later confirmation. Opposite-color or newly doji live quotes revoke confirmation at the shared account boundary.

The two-closed-candle contract, outer-rail predicate, live-doji guard, 0.10 ATR chase limit, same-close-bar restriction and all exits remain unchanged. Shared contract consumers cover scanner and account submission. No real test orders were submitted.

Validation: 98 tests passed across test_doji_same_color_confirmation.py (16), test_doji_entry_cleanup.py, test_live_third_entry.py and test_entry_without_wick_filter.py. The existing doji boundary expectation was updated only for closed dojis with same-color solid successors, as explicitly authorized. git diff --check passed. Referenced AIDAN specifications and the three historical channel test files are absent; no full-suite pass is claimed. Existing unrelated edits were preserved.

Separate exit investigation: the latest lobster paper trade opened at 2026-10-02 07:02:08 UTC+8 for 0.1129913 with 5x leverage and closed at 07:03:47 for 0.11184881, reason Channel Swing HARD_STOP MARGIN_LOSS. Configured margin loss ratio is 0.05, roughly 1% adverse price movement at 5x. Initial ATR stop was 0.111375798. The screenshot has no readable time, so exact screenshot identity remains unverified. No loss limit was changed pending the user's requested limit.
