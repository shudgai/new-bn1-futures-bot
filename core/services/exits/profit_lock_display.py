"""Read-only display of the confirmed swing profit protection threshold."""
import math
from core.services.exits.peak_trailing_exit import estimated_net_pnl


def profit_lock_display(position, fee, slippage):
    if str(position.get('entry_mode', '')).upper() == 'CHANNEL_SWING':
        return {'profit_lock_amount': None, 'profit_lock_status': '已停用'}
    result = {'profit_lock_amount': None, 'profit_lock_status': '未啟動'}
    try:
        state = position.get('peak_trailing_state') or {}
        line = state.get('profit_stop_price')
        if line is None:
            return result
        entry, qty, line = float(position['entry_price']), float(position['qty']), float(line)
        if any(not math.isfinite(v) or v <= 0 for v in (entry,qty,line)):
            return dict(result, profit_lock_status='待更新')
        sign = 1 if position['side']=='LONG' else -1 if position['side']=='SHORT' else 0
        if not sign:
            return dict(result, profit_lock_status='待更新')
        order = position.get('moving_profit_order') or {}
        status = '模擬保護單' if not position.get('exchange_managed') else '本地保護'
        if order.get('algo_id'):
            line = float(order['price'])
            status = '交易所已掛單'
        return {'profit_lock_amount': estimated_net_pnl(entry,line,qty,sign,fee,slippage),
                'profit_lock_status': status, 'profit_lock_price': line}
    except (KeyError, TypeError, ValueError, OverflowError):
        return dict(result, profit_lock_status='待更新')
