# Impulse Breakout and Confirmed Reversal

A symmetric impulse entry requires an original open inside KC, a directional body of at least 0.5 preceding closed ATR, and a latest quote strictly outside the target rail. Live candles and the latest closed impulse are supported. Lagging MA and KC slopes do not veto this entry. Post-profit gates remain mandatory.

Opposite impulses close Channel Swing positions using REVERSE_ON_BULLISH_BREAKOUT or REVERSE_ON_BEARISH_BREAKOUT. A fully confirmed close creates a durable, same-candle, single-use receipt. New entries revalidate fresh market data and account controls. Partial or unconfirmed closes never authorize a new position.

Before the net ROI peak reaches 5%, MA15 or KC-middle support suppresses ordinary pivot and lagging CK soft exits. Tiered locks remain 5%/1.2 percentage points, 10%/2.5 percentage points, and 15%/20% of peak ROI.

Validation: 178 targeted tests passed, including impulse boundaries, retreat, concurrent reversals, partial fills, persistence, receipt expiry and consumption, lifeline support, tiered locks and post-profit gates.
