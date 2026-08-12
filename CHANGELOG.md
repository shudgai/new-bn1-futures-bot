# Changelog

## [Unreleased] - 2026-08-12

### Changed
- Set `TRADE_AMOUNT_USDT` default to `50.0` (was 75.0). Commit: 1e860b4
- Set `MAX_TRADE_RISK_USDT` default to `2.0` (was 0.50). Commit: 1e860b4
- Restored `MIN_TRADE_USDT` default to `5.0` (was 20.0). Commit: 27840f0

### Notes
- Ran full test suite: 177 passed, 31 skipped.
- These changes increase per-trade capital and relax per-trade risk cap while preserving the prior minimum trade size.
