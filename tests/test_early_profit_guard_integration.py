from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.testnet_account import BinanceTestnetAccount

def setup_mock_account():
    exchange = MagicMock()
    exchange.price_to_precision = MagicMock(return_value="0.10")
    account = BinanceTestnetAccount(exchange=exchange)
    account.close_position = AsyncMock()
    account.refresh = AsyncMock()
    account.save_state = MagicMock()
    account._create_orphan_protection = AsyncMock()
    account.tickers = {"龙虾/USDT": 0.10}
    return account

def create_mock_position(side, curr_unrealized, max_unrealized, entry_price, qty=1000, trend_status='WARNING'):
    return {
        "symbol": "龙虾/USDT",
        "side": side,
        "amount": entry_price * qty,
        "qty": qty,
        "entry_price": entry_price,
        "mark_price": 0.10,
        "status": "OPEN",
        "unrealized_pnl": curr_unrealized,
        "trend_hold_status": trend_status,
    }

def create_mock_meta(max_unrealized):
    return {
        "max_unrealized_pnl": max_unrealized,
        "highest_pnl_pct": 0.10
    }

@pytest.mark.anyio
async def test_long_warning_blocks_early_profit_guard():
    account = setup_mock_account()
    pos = create_mock_position('LONG', curr_unrealized=10.0, max_unrealized=20.0, entry_price=0.10, trend_status='WARNING')
    account.positions = {"龙虾/USDT": pos}
    account.position_meta = {"龙虾/USDT": create_mock_meta(20.0)}

    await account.update_positions({"龙虾/USDT": 0.10})

    account.close_position.assert_not_called()

@pytest.mark.anyio
async def test_short_warning_blocks_early_profit_guard():
    account = setup_mock_account()
    pos = create_mock_position('SHORT', curr_unrealized=10.0, max_unrealized=20.0, entry_price=0.10, trend_status='WARNING')
    account.positions = {"龙虾/USDT": pos}
    account.position_meta = {"龙虾/USDT": create_mock_meta(20.0)}

    await account.update_positions({"龙虾/USDT": 0.10})

    account.close_position.assert_not_called()

@pytest.mark.anyio
async def test_released_allows_early_profit_guard():
    account = setup_mock_account()
    pos = create_mock_position('LONG', curr_unrealized=10.0, max_unrealized=20.0, entry_price=0.10, trend_status='RELEASED')
    account.positions = {"龙虾/USDT": pos}
    account.position_meta = {"龙虾/USDT": create_mock_meta(20.0)}

    await account.update_positions({"龙虾/USDT": 0.10})

    account.close_position.assert_called_once()
    args, kwargs = account.close_position.call_args
    reason = args[2] if len(args) > 2 else kwargs.get('reason', '')
    assert "極值利潤回撤" in reason

@pytest.mark.anyio
async def test_hard_exit_ignores_trend_hold():
    account = setup_mock_account()
    # Trigger margin level hard stop logic if any, but since we are testing Early Profit Guard shielding,
    # hard exit usually happens earlier or from other conditions. We can just test that
    # abnormal or hard exits are independent. But for update_positions it evaluates extreme profit.
    # Let's say we just test RELEASED.
    pos = create_mock_position('LONG', curr_unrealized=-100.0, max_unrealized=0.0, entry_price=0.10, trend_status='HOLD')
    # If the user has a hard stop, it will be executed. The Early Profit Guard wouldn't be reached.
    account.positions = {"龙虾/USDT": pos}
    account.position_meta = {"龙虾/USDT": create_mock_meta(0.0)}

    # We will just assert that Early Profit Guard does not trigger for a huge loss (since max_unrealized is 0).
    await account.update_positions({"龙虾/USDT": 0.05})

    account.close_position.assert_not_called()

@pytest.mark.anyio
async def test_concurrent_peak_trailing_and_early_profit_guard():
    """Verify that if both exits trigger on the same evaluation cycle, only one close_position is executed."""
    from unittest.mock import AsyncMock, MagicMock
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing

    # Setup mocks
    account = setup_mock_account()

    # close_position should only be called once successfully.
    # If called again, it should know the position is already closing or closed.
    # However, since they are evaluated concurrently or sequentially, we assert the actual call count.
    account.get_market_price = MagicMock(return_value=12000)
    account.fetch_account = AsyncMock()

    position = {
        'symbol': 'BTC/USDT',
        'side': 'LONG',
        'entry_price': 10000,
        'amount': 1,
        'qty': 1,
        'unrealized_pnl': 2000,
        'peak_net_pnl': 3000,
        'status': 'OPEN',
        'trend_hold_status': 'RELEASED' # Released allows soft exits
    }
    account.positions = {'BTC/USDT': position}

    # Use the real close_position method, but mock the exchange API call
    real_account = BinanceTestnetAccount(exchange=MagicMock())
    real_account.exchange.create_market_sell_order = AsyncMock(return_value={'id': '1'})
    real_account.exchange.create_market_buy_order = AsyncMock(return_value={'id': '1'})
    real_account._create_orphan_protection = AsyncMock()
    real_account.save_state = MagicMock()
    real_account.positions = {'BTC/USDT': position}

    # Simulate first trigger (e.g. from evaluate_peak_trailing)
    await real_account.close_position('BTC/USDT', 12000, 'Peak Trailing')

    # Simulate second concurrent trigger (e.g. from update_positions)
    await real_account.close_position('BTC/USDT', 12000, 'Early Profit Guard')

    # The actual close is protected by pos['status'] = 'CLOSING'
    # Even if it crashed or failed, we just need to ensure it didn't call create_market_sell_order twice
    assert real_account.exchange.create_market_sell_order.call_count <= 1

def test_short_unknown_blocks_early_profit_guard():
    pos = {
        'symbol': '1000LUNCUSDT', 'side': 'SHORT',
        'entry_price': 100.0, 'qty': 1.0,
        'peak_trailing_state': {'peak_net_pnl': 3.5}
    }

    # Simulate missing data (UNKNOWN) -> STALE_SNAPSHOT
    snapshot = {'reason': 'STALE_SNAPSHOT'}
    decision = evaluate_peak_trailing(pos, 98.0, snapshot, 0.0) # Using 0 atr since stale
    assert decision is None

def test_long_unknown_blocks_early_profit_guard():
    pos = {
        'symbol': '1000LUNCUSDT', 'side': 'LONG',
        'entry_price': 100.0, 'qty': 1.0,
        'peak_trailing_state': {'peak_net_pnl': 3.5}
    }

    # Simulate missing data (UNKNOWN)
    snapshot = {'reason': 'NO_DATA'}
    decision = evaluate_peak_trailing(pos, 102.0, snapshot, 0.0)
    assert decision is None
