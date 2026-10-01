# KC pending structural confirmation

## Authorized scope and implementation defaults

Replaces the active KC immediate/two-bar entries with two closed candles establishing a pending signal, followed by third/fourth closed-candle structural confirmation. No exit, initial stop, account loss limit, MA5 exit, profit lock, sizing or leverage code was modified. The old CLOSED_BODY_BREAKOUT and KC_2BAR_BREAKOUT submission codes are rejected so they cannot bypass pending confirmation. Other unrelated strategy implementations were not edited or re-enabled.

The user supplied 1-2 waiting bars and a 0.5 ATR distance example. This implementation uses two waiting bars (third and fourth), inclusive 0.5 ATR confirmation-close distance, and closed confirmation throughout. A small opposite body is at most 0.5 of that completed candle's ATR. These defaults were disclosed while requesting optional clarification. Flat candles outside the rail can wait but cannot authorize entry. Rail touches fail the strict outside condition.

First LONG body crosses the upper rail: open <= its upper rail < close and close > open. The immediately following closed candle must be green, close above its own upper rail, MA5 > MA15 and MA5 above first-candle MA5. This creates KC_BREAKOUT_LONG_PENDING without entry. The SHORT rule is exactly mirrored. Body crossing prevents repeated re-labeling of already-outside candles as new breakouts after expiry.

While pending, an outside same-direction closed body requires MA5/MA15 alignment and directional MA5 slope versus the immediately previous closed candle, plus at most 0.5 ATR close-to-rail distance, to emit KC_3BAR_CONFIRM_LONG/SHORT. Small opposite bodies still outside preserve pending through the next allowed candle; MA5 alignment must remain intact, while slope is checked only for entry. An inside/touch close, lost alignment, failed directional slope on a confirmation body, oversized opposite body, over-distance confirmation or expiry cancels the signal. A successful third-bar structural confirmation cannot be revived on the fourth if it did not fill; only same-bar order retry is allowed under existing account checks. Cancellation requires a fresh body crossing and new second confirmation, not reuse of the old pair.

## State and timing

core/services/kc_pending_entry.py::evaluate_kc_pending_entry replays explicitly closed consecutive bars to reconstruct pending state. It is deterministic and read-only: scans, diagnostics and account revalidation cannot extend its original expiry. Restart reconstructs the same bounded state from closed market data. The original first and second timestamps form pending_signal_id; successful persisted OPEN trades prevent that identity from opening again. The latest closed confirmation timestamp still drives the existing candle deduplication and freshness checks. No future candle or historical wick sequence is used.

Both the third and fourth confirmations wait for closure. A third-bar confirmation can only be submitted after its close, so actual fills may be timestamped in the next minute. Existing settlement REST verification and quote/account checks remain. The configured 0.5 ATR distance applies to the confirmation close and that completed candle's ATR/rail; it does not promise a market fill at that close.

## Modified functions

- Added evaluate_kc_pending_entry and above_limit in core/services/kc_pending_entry.py.
- evaluate_entry_contract in core/services/entry_contract.py delegates exclusively to the pending policy, exposes pending/cancellation diagnostics and checks persisted breakout identity.
- validate_account_entry in core/services/entry_firewall.py also rejects a changed original pending identity before submission. Snapshot evidence keys include original identity and wait count.
- No new engine mutation was necessary; the previous feature's generic evidence-copy path carries the pending fields.

## Verification

82 tests passed in tests/test_kc_pending_entry.py: symmetry, pending-only second candle, third confirmation, small counter-candle and fourth recovery, 0.5 ATR boundaries, rail return/touch, invalid MA/data, gap and finality, expiry without re-labeling, fresh breakout recovery, repeated read-only reconstruction, persisted-fill dedup, changed identity, old signal rejection, settled account revalidation, real engine isolated paper fills and balance/daily/slot/quote risk gates. All order tests were isolated or mocked; no real test orders.

Existing exit suite test_abnormal_only_hold.py: before 30 passed / 9 failed, after 30 passed / 9 failed, identical test outcomes. Before baseline was the saved pre-change entry contract loaded in an isolated test process. Per-test evidence is exits_before.json and exits_after.json. Previous entry tests describe superseded immediate-entry policies and are not asserted to pass under this intentionally changed policy. No full-repository pass is claimed. Referenced AIDAN specifications and historical channel test files remain absent.

Syntax compilation and git diff --check pass. Baseline source is in before/. Existing unrelated working-tree changes remain untouched. No git commit/push was performed. Service deployment evidence is in deployment.json.
