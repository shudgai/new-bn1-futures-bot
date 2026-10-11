# Restore CAP beside lobster

User requested replacing SUI with CAP with the same entry and exit conditions as lobster. Active .env and .env.example now select lobster/CAP. Added CAP to the shared channel symbol policy and changed realtime/account exit whitelists to use the common symbol set rather than duplicate literals. Existing SUI position is retained under normal management; engine already includes held symbols in price/candle/exit monitoring while only DEFAULT_SYMBOLS can open new positions. No position renamed or manually closed.

CAP uses the same second/third outside non-doji entry and remaining strict entry contract, confirmed post-entry pivot exits, disabled percentage lock and disabled position hard stop. 72 targeted tests passed, including configuration selection and long/short CAP/lobster entry evidence and pivot trigger equivalence.
