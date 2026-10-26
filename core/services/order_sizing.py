"""USD-M linear quantities are expressed in the quoted market's base units."""
from decimal import Decimal, InvalidOperation

from core.symbol_aliases import to_binance_usdm_symbol


def positive(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('[ORDER_SIZE] Invalid numeric input') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('[ORDER_SIZE] Input must be finite and positive')
    return result


def calculate_target_slot_capital(available_balance: float, slot_count: int = 2) -> float:
    """雙槽位 (2-Slots) 架構核心資金分配：
    Target_Capital = Account_Balance / 2
    每次開倉調用當前帳戶總可用餘額的 1/2。
    """
    avail = float(available_balance or 0.0)
    if avail <= 0:
        return 0.0
    slots = max(1, int(slot_count))
    return round(avail / float(slots), 4)


def raw_order_qty(margin, leverage, price, symbol: str = None):
    """計算目標開倉數量 (Quantity / Size)。
    
    CRITICAL: 1000PEPE 特殊單位換算處理：
    1. 幣安 USD-M 期貨中，1000PEPEUSDT 的合約乘數 (Contract Size) 為 1（即 1 手 = 1000 PEPE）。
    2. 行情中心的即時報價 price 本身即為「每 1000 顆 PEPE 的 USDT 價格」（例如 0.0095 USDT / 1000 PEPE）。
    3. 名義價值 Notional = qty * price (USDT)。
    4. 所需保證金 Margin = Notional / leverage = (qty * price) / leverage。
    5. 目標數量 qty = (Target_Capital * leverage) / price。
    
    ⚠️ 鐵律禁令：絕對不可在計算中額外除以 1000 或重複乘 1000！
    任何額外縮放都會導致 1000PEPE 的下單本金放大或縮小 1000 倍！
    """
    return positive(margin) * positive(leverage) / positive(price)


def calculate_order_qty(exchange, symbol, margin, leverage, price, *, market_order=True):
    exchange_symbol = to_binance_usdm_symbol(symbol)
    market = exchange.market(exchange_symbol)
    if market.get('linear') is not True or positive(market.get('contractSize')) != 1:
        raise ValueError('[ORDER_SIZE] Expected Binance linear base-unit contract')
    raw = raw_order_qty(margin, leverage, price)
    quantity = positive(exchange.amount_to_precision(exchange_symbol, str(raw)))
    filters = {item['filterType']: item for item in market['info']['filters']}
    required = ['LOT_SIZE', 'MARKET_LOT_SIZE'] if market_order else ['LOT_SIZE']
    steps = []
    for name in required:
        if name not in filters:
            raise ValueError('[ORDER_SIZE] Missing quantity filter: ' + name)
        rule = filters[name]
        step = positive(rule['stepSize'])
        steps.append(step)
        if (quantity < positive(rule['minQty']) or quantity > positive(rule['maxQty'])
                or quantity % step != 0):
            raise ValueError('[ORDER_SIZE] Quantity violates ' + name)
    # Precision may remove less than one lot, never shrink the order by a
    # multiplier or silently increase its allocated notional.
    if quantity > raw or raw - quantity >= max(steps):
        raise ValueError('[ORDER_SIZE] Precision changed the target notional')
    notional = quantity * positive(price)
    minimum = filters.get('MIN_NOTIONAL', {}).get('notional')
    if minimum is not None and notional < positive(minimum):
        raise ValueError('[ORDER_SIZE] Below minimum notional')
    return float(quantity)
