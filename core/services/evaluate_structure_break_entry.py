def evaluate_structure_break_entry(frame, symbol: str):
    """
    D0 STRUCTURE BREAK ENTRY
    Evaluates a live tick for a strict structure break against the exactly prior 3 CLOSED 1m candles.

    LONG: LIVE_PRICE > MAX(high of exactly prior 3 CLOSED 1m candles)
    SHORT: LIVE_PRICE < MIN(low of exactly prior 3 CLOSED 1m candles)

    Current live candle is excluded.
    Strict inequality. No other filters (MA, KC, Body, ATR, etc).
    """
    if len(frame) < 4:
        return None

    # The last row is the forming (live) candle.
    # The 3 rows before it are the prior 3 closed candles.
    closed_3 = frame.iloc[-4:-1]
    live_row = frame.iloc[-1]
    live_price = float(live_row['close'])
    live_stamp = live_row.name if live_row.name else live_row['timestamp']

    prior_3_high = float(closed_3['high'].max())
    prior_3_low = float(closed_3['low'].min())

    stamp1 = int(closed_3.iloc[0]['timestamp'])
    stamp2 = int(closed_3.iloc[1]['timestamp'])
    stamp3 = int(closed_3.iloc[2]['timestamp'])
    prior_3_bar_ids = f"{stamp1}_{stamp2}_{stamp3}"

    side = None
    reference_level = None

    if live_price > prior_3_high:
        side = 'LONG'
        reference_level = prior_3_high
    elif live_price < prior_3_low:
        side = 'SHORT'
        reference_level = prior_3_low

    if not side:
        return None

    d0_event_id = f"{symbol}_{side}_{prior_3_bar_ids}"

    return {
        'action': 'ENTER',
        'entry_phase': 'D0_STRUCTURE_BREAK',
        'd0_event_id': d0_event_id,
        'side': side,
        'reference_level': reference_level,
        'prior_3_bar_ids': prior_3_bar_ids,
        'trigger_price': live_price,
        'trigger_timestamp': float(live_stamp),
        'reason': f"D0 {side} break of {reference_level:.5f}",
        # Adding common fields for downstream processing:
        'price': live_price,
        'type': f"D0_STRUCTURE_BREAK_{side}",
    }
