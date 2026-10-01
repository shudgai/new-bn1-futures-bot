# Entry delay at 2026-10-02 06:57 UTC+8

Read-only investigation of the user's 06:57 report. Evidence was extracted from the running paper account's persisted logs and trade snapshots into evidence.json. No trading rules, positions, service state or existing working-tree edits were changed. Referenced AIDAN specifications were not present. System journal access was unavailable; account-persisted records supplied the evidence.

## 1000PEPE

The saved candle evidence confirms 06:54 closed above the upper rail (0.0044151 versus 0.0044115312), followed by another green candle at 06:55. The third candle was 06:56. At 06:56:06 the scanner recorded BLOCKED_OPEN_CHASE. The configured limit was 0.10 of the preceding closed ATR, approximately 0.000000433 above the third candle's open of 0.0044176. The logs do not preserve the exact rejected quote; historical highs cannot establish its tick sequence.

At 06:57:03 execution began, but the next snapshot failed. At 06:57:07 the scan recorded another chase rejection. A subsequent attempt reached account submission at 06:57:13, then failed the live-doji guard at 06:57:15; revalidation again rejected a live doji at 06:57:17. The paper fill occurred at 06:57:22 for 0.00441694, after a pre-slippage quote of 0.0044165.

Thus the user correctly identifies a fourth-candle fill relative to the 06:54 breakout. The implementation evaluates a rolling pair: the eventual fill identifies 06:55/06:56 as its closed pair and 06:57 as its third candle. This relabeling does not mean the original third candle filled. The current contract is two closed candles plus a live candle, not a requirement to wait for three closes.

## Lobster

At 06:57:07 and 06:57:17, among other observations, the scanner recorded BLOCKED_CLOSED_DOJI. Live-doji rejections alternated with that reason. Later saved closed evidence confirms the 06:56 candle had open 0.11037, close 0.11048, high 0.11140 and low 0.11024: body/range = 9.4827586%, below the current 10% threshold. Being green and above the upper rail does not bypass that guard. The later paper fill occurred at 07:02:08 at 0.1129913 with the 07:00/07:01 pair.

## Scope and next decision

The observed blockers are the existing chase-distance and doji policies. Removing those checks would change entry behavior; it is not an indexing fix established by this investigation. AGENTS.md explicitly says a missed-entry inquiry does not authorize relaxing entry restrictions. No policy relaxation, restart or deployment was performed. Numeric candle checks were calculated from saved evidence; no test orders or future-tick reconstruction were used.
