from decimal import Decimal
from types import SimpleNamespace
import pytest
from core.services.order_sizing import calculate_order_qty, raw_order_qty


def exchange(formatter=None, **changes):
    market = dict(linear=True, contractSize=1., info={'filters':[
        dict(filterType='LOT_SIZE',minQty='1',maxQty='10000000',stepSize='1'),
        dict(filterType='MARKET_LOT_SIZE',minQty='1',maxQty='10000000',stepSize='1'),
        dict(filterType='MIN_NOTIONAL',notional='5')]})
    market.update(changes)
    return SimpleNamespace(market=lambda symbol:market,
        amount_to_precision=formatter or (lambda symbol,qty:str(int(Decimal(qty)))))


@pytest.mark.parametrize('balance,leverage,expected', [(50,10,56818),(60,10,68181),(80,10,90909),(60,5,34090)])
@pytest.mark.parametrize('market_order',[True,False])
def test_half_wallet_quantity(balance,leverage,expected,market_order):
    q=calculate_order_qty(exchange(),'1000PEPE/USDT',balance*.5,leverage,.0044,market_order=market_order)
    assert q==expected
    assert 0 <= balance*.5*leverage-q*.0044 < .0044


@pytest.mark.parametrize('symbol',['1000PEPE/USDT','1000PEPE/USDT:USDT','1000SHIB/USDT'])
def test_no_symbol_prefix_multiplier(symbol):
    assert calculate_order_qty(exchange(),symbol,30,10,.0044)==68181


@pytest.mark.parametrize('formatted',['68','30','68182','68181.5','0','NaN'])
def test_reject_wrong_scale_round_up_or_step(formatted):
    with pytest.raises(ValueError,match='ORDER_SIZE'):
        calculate_order_qty(exchange(lambda *args:formatted),'1000PEPE/USDT',30,10,.0044)


@pytest.mark.parametrize('invalid',[0,-1,float('nan'),float('inf')])
def test_invalid_price_rejected(invalid):
    with pytest.raises(ValueError,match='ORDER_SIZE'):
        raw_order_qty(30,10,invalid)


def test_reject_unexpected_contract_unit():
    with pytest.raises(ValueError,match='ORDER_SIZE'):
        calculate_order_qty(exchange(contractSize=1000),'1000PEPE/USDT',30,10,.0044)


def test_min_notional_and_max_lot():
    with pytest.raises(ValueError,match='minimum notional'):
        calculate_order_qty(exchange(),'1000PEPE/USDT',.02,5,.0044)
    with pytest.raises(ValueError,match='LOT_SIZE'):
        calculate_order_qty(exchange(),'1000PEPE/USDT',100000,10,.0044)


def test_real_ccxt_precision_without_network():
    import ccxt
    ex=ccxt.binanceusdm()
    market=dict(id='1000PEPEUSDT',symbol='1000PEPE/USDT:USDT',base='1000PEPE',quote='USDT',
        settle='USDT',baseId='1000PEPE',quoteId='USDT',settleId='USDT',type='swap',spot=False,
        swap=True,future=False,option=False,contract=True,linear=True,inverse=False,contractSize=1.,
        precision={'amount':1.,'price':.0000001},limits={},info=exchange().market('x')['info'])
    ex.set_markets([market])
    assert calculate_order_qty(ex,market['symbol'],30,10,.0044)==68181
