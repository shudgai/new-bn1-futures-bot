def check_streamlined_entry_signal(df, side, live_price, **kwargs):
    # ...
    dist_from_middle = abs(prev_close - kc_mid_prev1)
    if body_length >= 2.0 * current_atr:
        if dist_from_middle > 2.0 * current_atr:
            # Overextended Special K
            target_price = prev_close - (body_length * 0.5) if side == "LONG" else prev_close + (body_length * 0.5)
            # return action "ENTER_LIMIT"
