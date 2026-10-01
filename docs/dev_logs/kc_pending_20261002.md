# Bounded KC pending confirmation

Changed active KC entry confirmation to a first body crossing, second closed MA-aligned confirmation and third/fourth closed structural confirmation. Small opposite bodies <=0.5 ATR outside the rail retain pending; cancel on invalid structure, chase >0.5 ATR or expiry after two waiting candles. Original breakout identity is stable and deduplicated from persisted fills. Old immediate KC codes cannot bypass the new policy. All held exits and account risk thresholds remain unchanged.

82 focused tests pass. Existing exit suite has identical before/after outcomes: 30 pass / 9 fail. See reports/kc_pending_20261002/README.md for defaults, timing and evidence.
