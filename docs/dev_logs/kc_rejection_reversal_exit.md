# KC-edge rejection and successor reversal exit

Channel Swing positions may now exit when a completed candle forms a rejection
near the position-side KC rail and the next live candle confirms adverse pressure.
The prior candle must be within 0.2 ATR of the relevant outer rail and have a
wick at least as large as its body or a body at the existing 10% doji boundary.
The next live quote must form an adverse body of at least 0.15 ATR or break the
prior candle's extreme. The exit uses the existing close lock and retry path.

This trigger is authorized through the Channel Swing exit whitelist and is
exempt from the strategy minimum-hold/grace filter. It does not use historical
live-tick reconstruction. The targeted pullback/realtime-exit selection passed
93 tests. The broader five-module compatibility run had 155 passes and 210
failures. It includes tests built around older entry behavior and at least one
unrelated source error; failures were not individually classified. No claim of
a clean full regression suite is made.

No deployment, service restart, or live order was performed.
