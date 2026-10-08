# Channel Swing trend hold and MA5 chop guard

## Owner decisions

### Latest superseding decision: closed MA5 turn only

- Policy `closed_ma5_post_entry_turn_v4` supersedes the historical rules below.
  Three latest completed post-entry MA5 values must form two strict legs:
  up then down for LONG, down then up for SHORT. All three candle opens must
  be at or after entry. Relative-price 1e-12 tolerance treats near-flat steps
  as flat. No live MA5, KC reversal or price pivot is required.
- Cancel doji/adverse-body exits and the 3-ATR maturity qualification.
  Revoke all earlier-policy pending exits. New position-bound MA5 claims
  persist for retry/restart; no profit lock or hard stop is restored.
  This is a local MA5 turn, not a guaranteed absolute market extremum.
- Mobile chart markers at width <= 768px show only 買多 / 賣空 / 平多 /
  平空 without time suffixes; desktop labels and times remain unchanged.
- Entry drafts, including SMALL/multi-bridge 0.5-ATR entry, remain preserved
  in stash `f62561c104732f643c41f8ec36e50f3483608771`, excluded from deployment.
- Validation: 206 passed across owner-policy, structured/specialized routes,
  V2 execution boundaries, staged implementation and isolated Testnet
  integration. No real exchange requests; existing datetime warnings remain.
  Browser verification at 390px captured all four compact labels.

### Historical superseded decisions

- Base: `8be77071bf1c0f9a42bf21076e1052eb512a4804`.
- Ordinary Channel Swing positions have no profit lock, peak giveback, initial ATR stop, account hard-stop, waterfall, or independent turn/reverse close authority.
- Manual close remains available. There is no software loss floor; exchange liquidation cannot be disabled.
- General exit requires the latest two completed KC middle values to reverse against the position and the latest three completed candles to confirm a post-entry peak (LONG) or trough (SHORT). All three candle opens must follow entry. Flat or invalid KC does not authorize an exit.
- Independent exception is enabled only after actual observed post-entry favorable movement reaches 3.0 fixed entry ATR, inclusive. LONG measures maximum observed quote minus entry; SHORT measures entry minus minimum observed quote. This is exit qualification, not a profit lock or drawdown threshold.
- Once qualified, a post-entry completed doji (body/range <= 25%) followed immediately by a live adverse body >= 0.60 prior completed ATR, with live body/range strictly above 25%, may close. Use original live open and current quote; no hindsight reconstruction.
- Persist fixed entry ATR, observed maximum favorable displacement, and maturity qualification per position. Do not import legacy peaks or reconstruct missed observations from candle wicks. Invalid entry ATR disables this exception, not the independent CK/pivot exit.
- All automatic entry authorities share one added chop check: six completed MA5 values, ignore relative-tolerance flat steps, block at two direction changes. Missing or invalid data blocks entry. No efficiency, overlap, KC-crossing, or extra location gate is added.
- Existing MA5 direction/strength qualifications remain unchanged.
- Explicitly opted-in staged positions and unrelated legacy modes retain their independent authority.

## Root-cause evidence

Lobster SHORT opened at Taipei 18:17:17, closed at 18:20:42, reopened at 18:21:08, and closed again at 18:24:03. Both closes were `EXIT_REALTIME_PEAK_TRAILING`; the first recorded trigger was `EXIT_PEAK_PULLBACK_PRESSURE`, quote 0.03893, observed minimum 0.03861.

Before the first close, completed 18:18/18:19 KC middle values were 0.03974796446852661 / 0.03966720594771455, still decreasing. The 18:19 candle body was 0.00004 over a 0.00014 range (28.57%), not the approved 25% exit doji. Under the new policy this close has no authority. Historical closed candles establish these facts, not the full intrabar quote sequence.

## Integration and migration

Scan and quote exits use one position-bound persistent evaluator. Valid failed exits retain evidence for retry; replaced positions cannot inherit claims. Account close boundaries reject retired strategy reasons even when legacy callers use `is_manual=True`. Actual UI manual close retains its explicit reason.

Startup and account updates remove retired pending stop/profit states and zero active local SL/TP lines while retaining historical trades and reference initial-risk metadata. Account-only updates cannot invent candle evidence. Shared account locks and existing retry handling remain in place.

The later 3-ATR owner decision revokes unqualified V1 doji retries on migration, but preserves matching verified CK/pivot retries. Completed trade records and trigger logs retain the observed exit evidence. This patch cannot undo already completed V1 trades.

Frontend retains the prior Gate display and Vue 3 price-update repair, and labels the revised policy.

## Validation

- Original release: 151 passed. The 3-ATR revision: 168 passed across owner-policy tests, structured/specialized routes, V2 boundaries, staged implementation, and isolated staged Testnet integration. Includes all four symbol/side maturity paths, below/exactly/above 3 ATR and 0.6 ATR, fixed ATR/restart isolation, no hindsight peaks, old-ticket migration, and scan/quote shared authority. No real exchange orders.
- The first broader run had 22 failures because the V2 fixture provided only five completed candles. Added a sixth historical fixture candle; did not weaken the six-candle gate or assertions.
- Historical mandatory `test_channel_swing.py`, `test_channel_position_path.py`, and `test_channel_swing_execution.py` are absent in this checkout. No full-suite claim.
- Existing UTC datetime deprecation warnings remain.
- Owner authorized commit and paper-only restart, including existing ordinary positions.
- AIDAN common/Python specification files are absent from this checkout; not claimed as newly read or modified.

## Permanent WAIT gates

This patch does not implement or activate independent WAIT.

```text
WAIT_LONG_GATE = BLOCK
WAIT_SHORT_GATE = BLOCK
LOBSTER_WAIT_GATE = BLOCK
CAP_WAIT_GATE = BLOCK
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
```

Doji PASS describes only the permanent specification lock, not runtime WAIT verification. Paper restart authorization is not live/Testnet authorization or WAIT readiness.
