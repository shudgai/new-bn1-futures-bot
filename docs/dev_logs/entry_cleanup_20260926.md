# Entry cleanup — 2026-09-26

Removed four unreferenced legacy strategy functions: special momentum engulfing,
strict/relaxed structural alignment, and extreme pin defense. Removed undefined
sideways-exemption references while retaining the MA price and consecutive-candle
checks. Registered the currently generated MA_CROSS_OR_ENGULFING codes; normalized
FIRST and CONTINUATION results with side and stable execution reason. Unknown
entry types remain blocked. Account guards and exits are unchanged.

Validation: focused regression covers symmetric MA price rejection without
NameError, MA cross acceptance, continuation metadata and execution allowlist.
The existing MA cross ATR suite reports 27 failed and 19 passed; this is not a
full-suite pass. Its expectations include shorter fixtures and older exit APIs.
Required AIDAN common/Python specification files are absent in this checkout.

Focused regression: 6 passed; compilation and diff whitespace checks passed.
Also corrected five undefined c2_* names in the pre-order audit log to the
existing curr_* snapshot variables, discovered after the first service reload.
