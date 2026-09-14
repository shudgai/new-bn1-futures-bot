def _channel_all_same_color_inside(frame, side):
    if len(frame) < 5:
        return False
    for i in range(len(frame) - 2, -1, -1):
        row = frame.iloc[i]
        open_p, close_p = float(row["open"]), float(row["close"])
        if "ema_20" in row and not pd.isna(row["ema_20"]):
            mid = float(row["ema_20"])
        elif "kc_middle" in row and not pd.isna(row["kc_middle"]):
            mid = float(row["kc_middle"])
        else:
            mid = (float(row["kc_upper"]) + float(row["kc_lower"])) / 2.0
        
        if side == "LONG" and close_p < open_p:
            return False
        if side == "SHORT" and close_p > open_p:
            return False
            
        if side == "LONG" and (open_p <= mid or close_p <= mid):
            return True
        if side == "SHORT" and (open_p >= mid or close_p >= mid):
            return True
    return True
