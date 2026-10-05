# Paper-start readiness

Target: feature/lobster-cap-two-slots-gates. No deployment or restart authorized yet.

Apply only the keys in paper_start.settings.env.fragment to the chosen deployment environment; do not replace credentials or an entire existing environment file. Keep PAPER_TRADING=true. Structured entry caps margin at half the wallet and the available balance including fees. Market data already uses mainnet public endpoints independently of USE_TESTNET. Live-order permission remains NO.

Public exchangeInfo checks confirmed mainnet CAPUSDT and 龙虾USDT perpetual USDT contracts have TRADING status. Demo has no CAPUSDT and 龙虾USDT is PENDING_TRADING. Contract status must be checked again at the authorized start.

Current production process 891666 has PAPER_TRADING=true, USE_TESTNET=false, MAX_SLOTS=1, DEFAULT_SYMBOLS=龙虾/USDT and cwd /home/shudgai999/project/new bn. Service parent is 891656. Disk paper_account.json had no positions or pending orders at inspection; that is a disk snapshot, not authoritative current memory exposure. Do not query /api/status or /api/prices as read-only: they call update_positions and may manage exits.

New /api/account-exposure returns account-memory exposure without update, refresh, stop management or orders. Paper memory is authoritative for its local paper ledger; testnet/live cache is explicitly UNVERIFIED and cannot substitute for exchange-authoritative queries.

Deployment preflight required_files refers to the new scoped branch inventory, not the old incomplete Phase 1–5 candidate. That old candidate remains separate and unverified; its 124/124 result is not inherited. The deployment tool only evaluates operator-supplied evidence, so collect it freshly for the exact committed build. Fail closed if current exposure or runtime source cannot be verified. No tools here start or deploy the bot automatically.

Before starting: verify the approved deployment target, stable account exposure and disposition of existing paper positions; validate clean source and required file manifest; provision Python dependencies; merge the paper-only two-symbol settings; verify market symbols and run the preflight with exact-commit evidence and explicit deployment authorization. Production startup auto-starts the engine, so approval covers both deployment and the 8006 service restart. No such approval has been granted.
