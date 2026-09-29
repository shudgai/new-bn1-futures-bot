# Order quantity and contract units — 2026-09-26

User request: half-wallet margin for every entry; verify 1000PEPE API units and lot size; deploy.

Evidence: no calculate_order_qty helper or additional /1000 conversion existed in the audited active paths. Both account implementations already calculated margin * leverage / price. The status API reported paper_trading=true. The 12:22:32 PEPE record contained margin amount=30.9958576001821 USDT and qty=34724.94398558, not qty=30. The 13:05:03 entry contained margin=68.68082226902894 USDT and qty=76797.50786911 at leverage=5. These are paper account records, not exchange fill proof.

Public Binance production and sandbox exchangeInfo/CCXT metadata both identify base=1000PEPE, contractSize=1, LOT_SIZE.stepSize=1 and MARKET_LOT_SIZE.stepSize=1. Quantity is in quoted 1000PEPE base units, not individual PEPE tokens; do not divide by 1000 again. Saved metadata: reports/order_sizing_20260926/exchange_rules.json. Official reference: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information

Changes:
- Every automated entry uses half of wallet balance, capped by available balance including entry fees; removed the old non-PEPE 0.98, high-beta and signal-size multipliers.
- Shared raw_order_qty computes margin * leverage / price. Binance market/limit entries use calculate_order_qty with contract-unit verification, CCXT formatting, LOT_SIZE/MARKET_LOT_SIZE, min notional and strict notional-preservation checks. Reject unexpected scale changes, rounding up, invalid values and dust orders; never silently resize by a symbol prefix.
- Paper entries use the same raw formula. Paper quantities remain fractional simulation quantities; real API quantities follow exchange steps.
- Trade history now displays quantity and fill notional separately from margin in USDT.

Examples at price=0.0044: wallet=60, margin=30, leverage=10 -> quantity=68181 after one-unit rounding, notional=299.9964 USDT. At leverage=5 -> quantity=34090. Wallet 50–80 at leverage 10 gives quantities 56818–90909; quantity ranges depend on leverage.

Validation: 121 passed across quantity/CCXT precision, MA3/ROE/TP1, half-wallet execution for PEPE and BTC, and startup tests. Python compilation and git diff --check passed. No live test order was sent and trading mode was not changed. This does not assert the historical full test suite passes.
