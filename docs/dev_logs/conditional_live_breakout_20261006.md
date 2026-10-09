# Conditional live KC breakout correction

The entry contract had disabled live body breakout evaluation and added a direct three-closed-candle high/low breakout return that bypassed KC/body conditions and normal post-exit checks.

Restored the shared live breakout helper and removed that fallback from the automatic entry contract. The gate uses the original live open inside both KC rails, strict quote breakout, and a directional body of at least 0.5 times the previous closed ATR. Existing CK direction validation remains. Alternating candle colors alone provide no permission. Cached live codes are revalidated against current quotes. Same-candle post-close reversals are blocked. Corrected the continuation MA3 helper call to include quote.

Validation: tests/test_conditional_live_breakout.py: 9 passed, including both directions, body boundary, gap, rail touch, quote retreat, close-bar guard, structure-only rejection, and account final validation. Python compilation passed.

Broader run including tests/test_continuation_entry.py and tests/test_kc_live_third_confirmation.py: 26 passed, 24 failed. Failures include v2 continuation phase expectations and legacy KC_3BAR_CONFIRM whitelist/third-candle expectations. No complete-suite pass is claimed. No service restart or deployment performed.

The required AIDAN specification paths are absent from this checkout; identity verified as shudgai999 / shudgai999@gmail.com. Existing unrelated workspace modifications were retained.

## Missed-breakout continuation follow-up

The execution boundary now persists a qualification only after the shared strict live breakout contract passes, before risk or order refusal. Previously the continuation evaluator required a qualification but no runtime caller wrote it. Continuation requires the same side and a later live candle, valid prior closed ATR, strict CK/MA5 direction and quote beyond both KC and MA5; existing MA3, color and distance guards remain. Diagnostic evaluation remains read-only.

Combined targeted regression: 13 passed. Compilation passed. No deployment or service restart.
