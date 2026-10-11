# Stop ordinary pivot pullback closes

Matched the user's screenshot against paper trade records: lobster SHORT opened 2026-10-10 06:07:10 Asia/Taipei and closed 06:12:03 with THREE_POINT_PIVOT; the third SHORT opened 06:18:40 and closed 06:22:03 with the same trigger. The second close at 06:15:47 was an older MARGIN_LOSS stop before the already deployed retirement of position hard stops.

Per user request, Channel Swing no longer generates THREE_POINT_PIVOT closes. Removed that trigger from both normal and SUI/lobster execution whitelists and classed it as a retired pullback exit; matching saved pending pivot exits are cleared during migration and persisted through existing metadata paths. Corrected the realtime rejected-trigger cleanup to use the state dictionary directly, allowing cleanup rather than a swallowed KeyError.

UI net ROE 5% activation and 1 percentage point giveback remains active. Waterfall, two adverse closed abnormal candles, opposite band and CK correction remain active. Position hard stops remain disabled. Strict entry gates remain unchanged. Other strategies retain the standalone pivot helper. No retrospective modification of historical trades.

Validation: 213 passed in targeted strict entry, metadata, margin, hard-stop retirement, net ROE protection, pivot policy, grace and realtime exit tests. New coverage retires persisted pivot retry state for LONG/SHORT on SUI/lobster/DOGE and holds unarmed profit on a confirmed pivot. Previous expectations that Channel Swing should close on a pivot were updated for this explicit policy change.
