# Abnormal-only holding policy

The user requested continued holding until an abnormal adverse candle. The screenshot's PEPE close was verified as CLOSED_BELOW_MA5 at 16:54:02 UTC+8. Removed independent MA/KC/doji/peak-profit exit authorities and routed held scans through the shared persistent tick evaluator. The stated default is an adverse original-open-to-quote body >= 1.2 prior closed 1M ATR, symmetrically LONG/SHORT. Retained initial/account hard stops and manual operations; entry filters remain unchanged.

258 relevant tests passed. Python and inline JavaScript syntax and scoped diff checks passed. Five legacy close-suite fixture failures and missing AIDAN/historical test files are disclosed separately. See reports/abnormal_only_hold_20261001/README.md, incident.json, tests.txt and deployment.json for details.
