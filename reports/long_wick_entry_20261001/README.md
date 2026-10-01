# Long-wick entry rejection

User request: do not open positions on long-shadow candles. This implementation defines a long wick as either upper or lower wick greater than or equal to the candle body, with relative 1e-12 boundary tolerance. This numeric definition is an implementation default, not a value measured from the screenshot or explicitly specified by the user.

The shared automatic-entry contract applies the rule symmetrically to LONG and SHORT: original breakout candle, closed confirmation candle, latest closed continuation candle, and live order candle evaluated with the latest quote. Existing doji rejection remains. Original breakout pairs with long wicks cannot authorize later continuation or post-exit reopening. Normal entry, scanning, idle strategy evaluation, engine revalidation, and paper/testnet order boundaries share this contract. No persistent invalidation lock is added; later valid conditions can be reevaluated. Manual-entry behavior and held-position exits remain unchanged.

Previously only the second candle's directional wick was blocked at one body; other wicks were blocked at two bodies. The common helper now checks either wick at one body. The redundant second-candle directional check was removed. Diagnostics identify live or closed LONG_WICK_OR_DOJI rejection.

Verification: 219 tests passed in tests/test_strict_entry_contract.py. Before the fix, the initial 110 added cases produced 74 failures and 36 passes; six further integration cases verify scanner and mocked exchange-order rejection. Coverage includes exact equality, 0.99/1.01 boundaries, low-price scaling, both wick directions and position sides, continuation, post-close reopening, changing quotes, recovery, and a wick appearing after signal generation. No exchange orders were sent by tests. Python compilation and explicit whitespace validation passed. The source and test are untracked in this pre-existing working tree, so git diff --check alone does not verify their contents.

The preceding settlement-completion-time repair remains included. Referenced AIDAN specifications and historical channel_swing / channel_position_path / channel_swing_execution test files are absent; this is not a full repository test pass. The screenshot was not matched to timestamped execution evidence, so it is not proof of a specific historical fill's cause.

Deployment evidence is recorded separately in deployment.json. Existing unrelated working-tree edits were preserved; this task does not certify their exit policies. No commit or push was performed.
