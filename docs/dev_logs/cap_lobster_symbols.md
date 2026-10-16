# Restore CAP beside lobster

User requested replacing SUI with CAP with the same entry and exit conditions as lobster. Active .env and .env.example now select lobster/CAP. Added CAP to the shared channel symbol policy and changed realtime/account exit whitelists to use the common symbol set rather than duplicate literals. Existing SUI position is retained under normal management; engine already includes held symbols in price/candle/exit monitoring while only DEFAULT_SYMBOLS can open new positions. No position renamed or manually closed.

CAP uses the same second/third outside non-doji entry and remaining strict entry contract, confirmed post-entry pivot exits, disabled percentage lock and disabled position hard stop. 72 targeted tests passed, including configuration selection and long/short CAP/lobster entry evidence and pivot trigger equivalence.

### [2026-10-16 21:54:27 UTC+8] - Modification Phase: Binance Lobster and PEPE market integration
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `core/symbol_aliases.py` (`to_binance_usdm_symbol`); `core/services/market_data_service.py`; `core/engine.py` (market-data symbols, ticker fetch and short-cache fallback); `core/services/order_sizing.py`; `core/testnet_account.py` (precision, leverage, book and order boundaries); `tests/test_wild_swing_kline_stream.py`.
- **Modification Description**: Mapped the internal Chinese Lobster alias to Binance USD-M `龙虾/USDT:USDT` and 1000PEPE to `1000PEPE/USDT:USDT`; kept CAP as a distinct market. Applied the mapping consistently to REST/WebSocket candles, quotes, sizing, precision, leverage, order books and order submission. Fresh but undersized WebSocket caches now fall back to REST instead of returning too few candles to the chart.
- **Trigger Reason & Requirement**: User clarified that Lobster and CAP are different assets and supplied the exact Binance contract symbols; the chart API had returned 400 for Lobster and only one bar for PEPE.
- **Verification & Test Status**: Focused suite — 144 passed with 14 pre-existing matplotlib/pyparsing deprecation warnings. Binance USD-M public market lookup confirmed active `龙虾/USDT:USDT` and `1000PEPE/USDT:USDT`. After restarting `binance-8006.service`, `/api/status` reports both symbols, paper trading, and two slots; `/api/klines` returns HTTP 200 with 200 bars for each. No live orders were submitted.
