def calculate_dynamic_margin(wallet_balance: float, entry_price: float, sl_price: float, leverage: int, fee_rate: float, slippage: float) -> float:
    max_risk = wallet_balance * 0.01
    stop_pct = abs(entry_price - sl_price) / entry_price if entry_price > 0 else 0
    loss_pct_on_notional = stop_pct + 2 * fee_rate + slippage
    if loss_pct_on_notional <= 0:
        return 0.0
    target_notional = max_risk / loss_pct_on_notional
    return target_notional / leverage
print(calculate_dynamic_margin(150, 100, 98, 10, 0.0005, 0.001))
