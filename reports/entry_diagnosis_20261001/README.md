# Entry diagnosis against the current three-bar revision

During the ongoing continuation task, repository HEAD advanced to b06a7dd (preceded by 1d16395, introducing strict three-bar entries). entry_contract.py was modified at 15:42:16 UTC and the service restarted at 15:42:27 UTC. Therefore the earlier 318-test result belongs to the preceding continuation implementation and does not certify the currently running version. This investigation preserves the later policy changes.

The captured API snapshots show why current signals are absent:
- PEPE: the three latest closed body ratios are 88.95%, 95.05%, and 5.45%. The third is red (open 0.0044352, close 0.0044349), so it fails the current requirement of three same-direction closed bodies >=35%, even though price is above the upper rail.
- 龙虾: the first closed bar closes 0.10681 below its upper rail 0.10690537; subsequent bars are red, and the last body ratio is only 6.21%. The live quote 0.1047 is between KC lower 0.10356312 and upper 0.10712912. This quote location is contextual evidence; the current evaluator rejects on its closed-bar predicates, not an explicit current-price rail check.

There is also a deterministic runtime defect: a valid LONG or SHORT signal uses undefined latest_close in its return dictionary. Two isolated valid-three-bar cases reproduced NameError before the fix. The only trading-code change in this investigation replaces that reference with float(latest.close); no thresholds, entry/exit policies or account parameters are changed.

Verification: both new signal regressions pass. Combined with the previous abnormal-only suite, 32 tests pass and 9 fail. All 9 exit-suite failures also reproduce with the old entry expression restored solely in an isolated test process (30 pass / 9 fail); they reflect later exit-policy changes and are not repaired here. Python compilation and scoped diff checking pass. Missing AIDAN specifications and historical tests remain unresolved; no full-suite pass is claimed.

The generic WAIT_CLOSED_BREAKOUT_OR_CONTINUATION message obscures precise reasons. The current code also resets BLOCKED_OPEN_CHASE to the generic message at the end of evaluation. These diagnostic limitations were observed but not used to infer an unobserved fill or relax strategy conditions.

Read-only candle evidence is in pepe_snapshot.json, lobster_snapshot.json and findings.json. Deployment evidence is in deployment.json. No commit or push was performed.
