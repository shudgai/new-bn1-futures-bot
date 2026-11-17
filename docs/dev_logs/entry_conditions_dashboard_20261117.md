# Entry conditions shown in chart dashboard — 2026-11-17

Request: display the conditions currently required before a new position can open.

Changes:
- Added a flat-position chart panel showing KC direction, shared signal and fresh-quote revalidation, account slot/budget/market-data checks, and the latest cached entry blocker.
- Included the translated blocker label and detail in the Kline API response so the panel can explain why the latest evaluation is waiting or blocked.

Verification: Vue template compilation, Python compilation, and `git diff --check` passed. One unrelated existing continuation-contract test fails with `BLOCKED_ENTRY_WITHOUT_FRESH_OUTER_BREAKOUT_OR_MA_CROSS`.
