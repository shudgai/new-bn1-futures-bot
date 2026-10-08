# Channel Swing trend hold and MA5 chop guard

## Owner decisions

### Removing the stale six-MA5-turn veto after the 23:44 review

The Owner approved removing the standalone six-completed-MA5/two-turn veto
from all shared automatic entry paths. Keep the three-bar MA5/MA15
entanglement guard and its directional live-separation requirement unchanged.
Preserve formation, latest closed/live MA5 direction/strength, account
firewall, capital/slot checks and fill dedupe. Exit V9 is unchanged.
The old helper remains only as compatibility/test diagnostic, not order
authority. No historical order is backfilled.

Completed candle review: MA5 at 23:38..23:43 was 0.040062, 0.040164,
0.040180, 0.040162, 0.040404, 0.040612. The old rule counted two switches
around the 0.000018 retreat at 23:41 despite renewed advances at 23:42/43.
MA5/MA15 gap grew from 0.000026 at 23:41 to 0.000164 and 0.000299.
K1 23:42 crossed outside and K2 23:43 confirmed, with body/range ratios
about 82% and 30%. This establishes completed structure, not the exact
23:44:06 quote or guaranteed fill; live doji/color checks may still block
general entry at other quotes.

Validation: 274 passed in live-cross, owner-policy, entanglement, observed
provenance and V2 execution-boundary modules; 11 existing UTC datetime
warnings. New four-symbol/direction cross/general account-firewall checks
prove old two-turn histories no longer veto qualified entries, while
entanglement and current flat MA5 remain blocked. Whitespace/editor checks
passed. This targeted result is not a new full-suite result. Release entry
policy is `live_ma5_ma15_cross_or_observed_two_bar_v3`; exit stays V9.
Publish and restart only the paper service, backing up the stopped account.
Independent WAIT gates below remain BLOCK/NOT_TESTED.

### Final entry replacement and KC trend hold (after 23:24 release)

The Owner cancelled all SMALL/multi-bridge 0.50-ATR automatic live breakout
authority. Final whitelist is live MA5/MA15 cross, general two completed
breakout bodies, and continuation with observed general-pair provenance.
Retired live codes and cached snapshots fail at the account firewall. Saved
SMALL state remains inert historical data; runtime no longer advances it.

Live cross compares latest completed MA5/MA15 relationship to quote-repriced
MA5 (four completed closes + quote)/5 and MA15 (14 completed closes +
quote)/15. LONG crosses from below/touch to strictly above; SHORT mirrors.
Both live averages must move strictly in direction; existing closed/live
MA5 direction/strength and chop gates remain. KC location is not a cross
qualification, and candle color/doji/body confirmation is not inherited from
the retired breakout pattern. Stable symbol/side/reference/live-bar identity
and saved fills prevent same-cross reentry, including after same-bar close
and restart. A cross that remains qualified can be evaluated later in that
forming candle; a following candle already crossed is not a fresh cross.

New shared entry entanglement guard: all latest three completed
abs(MA5-MA15) <= each bar's 0.10 ATR, inclusive, blocks. It may resume only
when both quote-repriced averages move in entry direction and their signed
gap strictly exceeds 0.10 latest completed ATR. Exactly 0.10 does not unlock.
Never bypass formation, MA5 or account checks on unlock. Invalid required
data blocks explicitly; no chart-pixel or retrospective threshold.

Exit policy `kc_reverse_observed_ma5_peak_turn_010_atr_v9` requires latest
two consecutive completed KC middle values to reverse strictly against the
position AND the approved observed MA5 peak/trough retreat >= 0.10 fixed
entry ATR with adverse MA5 direction. KC forward/flat holds through MA5
pullbacks. Live KC does not establish reversal. All automatic entry types
share this exit. Preserve V8 peak/fixed-ATR/cursor observations, but revoke
its ungated pending exits. New fully-qualified V9 pending exits retain retry
authority. No independent selling-pressure exit is added. Waiting for closed
KC reversal can delay exit and increase giveback; it cannot guarantee the
historical 21:38 extreme or all first-breakout entry prices.

Ledger audit: CAP 22:43:30 LONG and lobster 22:46:32 SHORT were old generic
live-body entries; lobster 23:16:40 SHORT was old channel-turn. All predate
the 23:24:21 paper-only `152dfdc` release, not evidence of that release
executing retired routes. CAP 22:17:21 ledger action is SHORT, distinct from
the neighboring chart LONG marker. Old snapshots did not include MA5;
new saved closed evidence includes it. No historical fills are rewritten or
intrabar sequence reconstructed from future candle wicks.

V8 and SMALL integration sections below describe superseded releases only.
Permanent independent WAIT gates below remain BLOCK/NOT_TESTED.

Final cross/KC integration validation: 407 passed, zero failed, across
entanglement, live cross, observed provenance, live MA5 exits, owner policy,
V2 boundary, structured/specialized routes and staged implementation/
isolated Testnet integration. Eleven existing UTC datetime warnings remain.
All four symbol/direction paths include real paper runner fills; persisted
cross fills survive disk restart and cannot reuse same-bar close identities.
Checks include strict/touch/new cross, lost quote qualification, KC-inside
cross, signed live separation at/below/above 0.10 ATR, KC flat/forward hold,
completed-only KC reversal and V8 observation/pending migration.

The first integration run had six failures: four routing fixtures still used
retired live-body codes, and two risk fixtures submitted 101.5 despite a
valid current quote 101.8, failing MA5 strength before reaching capital/order
checks. Fixtures now use supported general codes and their actual current
quote; risk and no-order assertions remain intact. The initial collection
syntax error from helper placement was fixed before passing validation.
No full-suite claim, skipped tests, real exchange orders or independent
WAIT readiness claim. Publish/restart remains paper-only.

### Final superseding exit: observed MA5 peak/trough, 0.10 entry ATR

The Owner replaced flat-immediate-close with hold-flat/confirmed-peak exit.
Policy `observed_ma5_peak_turn_010_atr_v8` requires an actual post-entry
favorable MA5 observation, followed by retreat from the observed MA5 peak
(LONG) or trough (SHORT) >= 0.10 fixed entry ATR, inclusive, and adverse live
MA5 direction relative to latest completed MA5. Flat and sub-threshold
pullbacks do not close; no favorable observation means no peak authority.
This measures MA5 movement, not price movement or profit percentage.

Freeze the preceding completed ATR saved at entry. Missing/invalid entry ATR
blocks this authority explicitly; never substitute a later ATR. Persist
position-bound baseline/extreme, favorable observation, completed cursor and
pending close evidence strictly. Preserve verified V8 retries across restart,
but revoke older flat/turn-only pending reasons. Replay only validated
completed post-entry MA5 observations; never invent missed live quotes or
historical fills. Entry state/persistence failure blocks new entry, not the
independent held-position exit evaluation. Staged and manual close remain
separate. Entry still blocks flat/opposite MA5.

The live 0.10-ATR rule does not wait for candle close. Completed post-entry
evidence may also confirm the same qualified retreat for restart recovery.
Neither route guarantees an absolute market extremum or prevents all
giveback/loss. No profit lock or software hard stop is added. V7 below is
historical and is not the final integrated deployment policy.

Final integrated validation: 379 passed, zero failed, in the nine modules
listed in the entry integration section below; 11 existing UTC datetime
deprecation warnings remain. Covers 0.099/0.100/0.101 ATR MA5 retreat,
frozen ATR despite later indicator/position changes, live flat hold,
missing ATR rejection, actual paper restart preservation and close dedupe,
old flat-ticket revocation, all four symbol/direction paths, and entry
provenance failures without skipping held-position exit evaluation.
Stale/future frames cannot mutate runtime provenance or account-boundary
state. Git diff whitespace and editor diagnostics passed.

Release procedure: commit/push the integrated source, stop only the paper
service, back up its stopped ledger, restart and verify API entry/exit policy,
paper-only running status, new process source, persisted state and retained
historical trade IDs. Runtime evidence belongs to the session artifacts;
passing tests alone is not deployment verification or live trading approval.

### Final entry integration: approved exception and qualified continuation

The Owner reconfirmed the live-pattern exception after the sideways-chart
review. Shared automatic authority is limited to:

- SMALL same-direction setup, then one or more consecutive opposite SMALL
  bridges, then an observed live directional body >= 0.50 fixed preceding
  completed ATR. SMALL is NOT DOJI and <= 0.25 its own preceding completed ATR.
  No maximum bridge count. Live raw open may be outside KC; current quote
  must be strictly beyond the same-side KC rail and live MA5.
- General K1 completed directional body crosses from inside KC; completed
  K2 same-direction body confirms outside. Both body/range ratios >= 20%.
  Evaluate on forming K3, revalidating current quote and existing MA5 gates.
- Continuation requires a persisted observed general K1/K2 source, current
  KC/MA5 direction, and quote strictly beyond KC and live MA5. Touch/return
  to KC or completed KC reversal cancels the source; the same pair cannot
  resurrect it. Holding may invalidate an origin, never create a new one.

Scan, quotes, reentry, cached execution and the account firewall share this
contract. Retired channel-turn and three-bar aliases are not whitelisted.
A generic single giant candle and unqualified outside-KC quote have no
automatic authority. Persist both provenance stores strictly before exposing
new qualification; retain completed fill identities across restart. One live
BIG candle cannot be re-entered after its confirmed fill. Existing closed and
live MA5 direction/strength and six-bar chop guards remain; no new visual
sideways threshold is invented.

The saved stash remains a backup, but its entry draft has now been restored
and integrated. Earlier "excluded/stashed" statements below describe those
earlier deployments only. Exit is now V8 above. Mobile action labels remain
unchanged; the chart badge describes hold-flat and 0.10-ATR peak retreat.

Earlier entry integration validation: 333 passed across observed provenance, small/multi-
bridge, live-MA5 exit, owner-policy, V2-boundary, structured/specialized routes,
staged implementation and isolated Testnet integration. Includes actual paper
runner fills and dedupe independently for both symbols/directions and both
general/live entries. Eleven existing datetime deprecation warnings remain.
The historical named channel-swing/position-path/execution modules are absent
from this checkout. No full-suite or actual exchange-order claim.

Deployment target is only `binance-8006-paper.service`; no live/Testnet
activation. Independent permanent WAIT is not implemented by this pattern.
Its gates remain BLOCK/NOT_TESTED as listed below; doji PASS is specification/
static-helper trace only, never runtime WAIT readiness.

### Final superseding decision: live MA5 flat/adverse exit

Policy `live_ma5_flat_or_adverse_v7`: MA5 = (last four completed closes +
fresh current quote) / 5, compared to latest completed MA5. Flat or adverse
movement closes immediately, not after candle close, a V shape, KC or doji.
LONG adverse is down; SHORT adverse is up. Flat uses relative-price 1e-12
tolerance. All four symbol/direction paths share the evaluator.
Persist evidence and retry through account locks. Restart may replay
validated completed post-entry history, never unseen historical live quotes
or execution prices. Preserve the closed cursor across rolling windows;
gaps explicitly block replay. Consistent zero-range candles are valid.
Automatic entry requires both closed and live MA5 direction; original live
strength stays unchanged. Flat/opposite/invalid data blocks fresh submission.
No doji exit, profit lock or hard stop is restored. Intrabar noise, outages
and execution latency remain risks; profit retention is not guaranteed.

CAP 20:26 close was old V4: MA5 0.077280 -> 0.077336 -> 0.077236,
ledger net PnL -3.9975 USDT. Lobster 20:24 close was also old V4:
0.038952 -> 0.038954 -> 0.038948, not a doji trigger.
Historical trades are unchanged. At that V7-only deployment, entry drafts
remained separately stashed; the later integration above supersedes this scope.

V7 verification: 254 passed in the seven targeted owner-policy/live-MA5,
structured/specialized, V2-boundary and staged regression modules. Tests
cover live flat/adverse decisions without candle close, both symbols/sides,
invalid/stale/candle identity, restart retries, replaced-position isolation,
real paper close deduplication, closed cursor replay and flat entry rejection.
No full-suite or independent WAIT readiness claim.

### Latest amendment: MA5 flat/adverse closes, flat blocks entry

Policy `closed_ma5_flat_or_adverse_v6` supersedes V4 below. Any two
completed post-entry MA5 values that are flat (relative-price 1e-12 tolerance)
or adverse authorize close, without a prior favorable leg, V shape, KC
reversal or price pivot. LONG adverse is down; SHORT adverse is up.
Replay validated completed post-entry history chronologically on restart;
persist the MA5 cursor and pending evidence across rolling history windows.
Never backfill an execution price; execute/retry using a fresh current quote.
History gaps and invalid data suspend evaluation explicitly. Zero-range but
consistent OHLC is valid for this MA5 rule, not rejected as invalid doji.

Automatic entries require the latest two completed MA5 values to move
strictly in entry direction as well as the existing live-MA5 qualification.
Flat/opposite/invalid closed MA5 blocks scan and fresh account submission.
Manual entries retain their separate authority. Unfinished entry drafts
remain in stash, excluded from this change.

No guarantee of preventing all giveback: missing market data, outages,
jumps and execution latency remain possible. No profit lock is introduced.

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
