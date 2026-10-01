# Doji entry guard and local cleanup — 2026-10-01

Restored the inclusive 25% body/range doji entry veto for the live submission candle and three closed confirmation candles, shared with the compatibility entry path. Ordinary non-doji long wicks remain eligible; no separate wick-length or 35% body filter was restored.

Consolidated mirrored long/short three-bar checks; removed unused MA reads, an unused reversal helper, and an unreachable continuation branch. Chase rejection now retains BLOCKED_OPEN_CHASE diagnostics. Existing absolute 0.30 ATR compatibility strength threshold and holding exits are unchanged.

Validation: 86 targeted tests passed, including doji boundaries, both directions, scanner and account/exchange revalidation with mocked orders. 432 before/after refactor comparisons (192 accepted) matched, holding the restored doji policy constant. Syntax compilation and git diff --check passed. This is not a full-repository pass; previously documented exit-policy mismatches remain outside this change.

The paper-trading 8006 service was restarted and API health verified; see deployment.json. No real exchange test orders were sent. No commit or push performed.
