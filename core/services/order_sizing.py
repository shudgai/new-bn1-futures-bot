"""USD-M linear quantities are expressed in the quoted market's base units."""
from decimal import Decimal, InvalidOperation


def positive(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('[ORDER_SIZE] Invalid numeric input') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('[ORDER_SIZE] Input must be finite and positive')
    return result


def raw_order_qty(margin, leverage, price):
    # The 1000 prefix is already reflected in the quote price. Never divide
    # by a parsed symbol prefix or by 1000 again.
    return positive(margin) * positive(leverage) / positive(price)


def calculate_order_qty(exchange, symbol, margin, leverage, price, *, market_order=True):
    market = exchange.market(symbol)
    if market.get('linear') is not True or positive(market.get('contractSize')) != 1:
        raise ValueError('[ORDER_SIZE] Expected Binance linear base-unit contract')
    raw = raw_order_qty(margin, leverage, price)
    quantity = positive(exchange.amount_to_precision(symbol, str(raw)))
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
