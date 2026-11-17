# Test trading budget capped at 150 USDT — 2026-11-17

Request: restore the Binance Demo trading budget to 150 USDT while keeping the exchange wallet balance intact.

Changes:
- Set `TEST_BUDGET_CAP_USDT=150.0` in `.env` and `.env.example`.
- Apply the cap before per-slot margin sizing and in the execution fallback, so automatic and manual orders cannot size from the full Demo wallet.
- Keep API wallet and available balance reporting tied to Binance's real Demo wallet.
- Added a regression test for a 4,713.81 USDT wallet with a two-slot configuration.

The cap limits new-entry budget; it does not reset Binance Demo funds or change existing positions.
