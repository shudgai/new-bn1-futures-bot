# Long-wick automatic-entry filter

Changed the shared entry contract to reject either wick at least as long as the body, symmetrically for LONG/SHORT and across breakout, confirmation, continuation, and live order candles. The threshold is a stated implementation default for the user's long-shadow prohibition. Existing doji rejection, account risk checks, exits, and manual operations remain unchanged.

219 isolated entry regressions passed; Python compilation and explicit source/test whitespace checks passed. Missing AIDAN specifications and historical tests remain documented. See reports/long_wick_entry_20261001/README.md for evidence and scope, and deployment.json in that directory for service verification.
