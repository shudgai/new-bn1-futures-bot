import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core import config
from core.engine import TradingEngine
from core.services.structure_risk_sizing import structure_risk_plan, FULL_SLOT_POLICY
from core.services.exits.hard_stop_service import hard_stop_reason
from core.services.entry_contract import evaluate_entry_contract
from test_lobster_cap_gates import frame


@pytest.fixture(autouse=True)
def isolated_shadow_logs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'logs').mkdir()


@pytest.mark.parametrize('wallet,available,leverage', [(200., 200., 5.), (200., 100., 10.), (60., 60., 5.)])
def test_each_slot_budget_includes_its_own_entry_fee(wallet, available, leverage):
    margin = TradingEngine._half_wallet_entry_margin(wallet, available, leverage)
    assert margin+margin*leverage*config.TAKER_FEE_RATE == pytest.approx(wallet*.5)
    assert margin < wallet*.5


@pytest.mark.parametrize('available', [20., 99., 0., float('nan')])
def test_no_tiny_replacement_order_when_half_budget_unavailable(available):
    assert TradingEngine._half_wallet_entry_margin(200., available, 5.) == 0.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('stop_distance', [1.5, 3., 20.])
def test_structure_distance_never_shrinks_full_slot_and_loss_limit_stays_five_percent(side, stop_distance):
    sign = 1 if side == 'LONG' else -1
    plan = structure_risk_plan(100., side, 1., 100-sign*stop_distance,
                              80., 5., .05, .0005, .0001, preserve_margin=True)
    assert plan['amount'] == 80.
    assert plan['structure_risk_policy'] == FULL_SLOT_POLICY
    assert plan['structure_risk_budget_usdt'] == 4.
    p = dict(side=side, entry_price=100., qty=4., margin=80., leverage=5.,
             entry_mode='CHANNEL_SWING', **{k: v for k, v in plan.items() if k.startswith('structure_')})
    assert hard_stop_reason(p, 100-sign*.99) is None
    assert hard_stop_reason(p, 100-sign*1.) == 'MARGIN_LOSS'


def make_engine(account, monkeypatch, side='LONG', symbol='CAP/USDT', entry_frame=None):
    import core.engine as module
    monkeypatch.setattr(module, 'DEFAULT_SYMBOLS', ['龙虾/USDT', 'CAP/USDT'])
    monkeypatch.setattr(module, 'MAX_SLOTS', 2)
    if entry_frame is None:
        from test_entry_chop_gate import eligible_frame
        f = eligible_frame('pair' if symbol == 'CAP/USDT' else 'live', side)
    else:
        f = entry_frame
    now = float(f.iloc[-1].timestamp)/1000+1.
    monkeypatch.setattr('time.time', lambda: now)
    quote = float(f.iloc[-1].close)
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=now*1000)
    decision = evaluate_entry_contract(f, account=account, symbol=symbol)
    assert decision
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *a: 5.)
    engine.tickers = {symbol: quote for symbol in module.DEFAULT_SYMBOLS}
    engine._channel_entry_quote_times = {symbol: now for symbol in module.DEFAULT_SYMBOLS}
    engine.strategy = SimpleNamespace(compute_indicators=lambda f: f)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._fresh_channel_entry_snapshot = AsyncMock(return_value=dict(frame=f, decision=decision, price=quote))
    engine._entry_boundary_frame = AsyncMock(return_value=f)
    signal = dict(side=side, entry_mode='CHANNEL_SWING', signal_code=decision['type'],
                  candidate_bar_id=decision['confirmation_bar_id'], score=100)
    return engine, signal


def test_simultaneous_lobster_cap_orders_use_half_inside_shared_lock(monkeypatch):
    account = SimpleNamespace(positions={}, pending_limit_orders={}, trades=[], logs=[],
                             log=Mock(), wallet=200., available=200.)
    account.get_wallet_balance = lambda: account.wallet
    account.get_available_balance = lambda: account.available
    async def refresh(**kwargs):
        assert kwargs == {'force': True}
        assert engine._account_entry_submit_lock.locked()
    account.refresh = AsyncMock(side_effect=refresh)
    amounts = []
    async def submit(**kwargs):
        await asyncio.sleep(0)
        assert engine._account_entry_submit_lock.locked()
        amount, leverage = kwargs['amount_usdt'], kwargs['leverage']
        fee = amount*leverage*config.TAKER_FEE_RATE
        assert amount+fee <= account.available
        amounts.append(amount)
        account.available -= amount+fee
        account.wallet -= fee
        account.positions[kwargs['symbol']] = dict(margin=amount)
        return True
    account.open_position = AsyncMock(side_effect=submit)
    engine, signal = make_engine(account, monkeypatch)
    async def run():
        return await asyncio.gather(*(engine._place_structured_entry_locked(symbol, signal, 101.5)
                                      for symbol in ['龙虾/USDT', 'CAP/USDT']))
    assert asyncio.run(run()) == [True, True]
    assert amounts[0] == pytest.approx(100./(1+5*config.TAKER_FEE_RATE))
    assert amounts[1] == pytest.approx((200.-amounts[0]*5*config.TAKER_FEE_RATE)*.5/(1+5*config.TAKER_FEE_RATE))
    assert account.available >= 0
    assert len(account.positions) == 2
    assert account.refresh.await_count == 2


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_real_paper_fill_preserves_full_half_not_structural_risk_shrink(symbol, side, monkeypatch, tmp_path):
    import core.paper_account as paper
    monkeypatch.setattr(paper, 'STATE_FILE', str(tmp_path/'paper.json'))
    account = paper.PaperAccount()
    account.balance = 200.
    engine, signal = make_engine(account, monkeypatch, side, symbol)
    assert asyncio.run(engine._place_structured_entry_locked(symbol, signal, engine.tickers[symbol]))
    p = account.positions[symbol]
    target = TradingEngine._half_wallet_entry_margin(200., 200., 5.)
    assert p['margin'] == pytest.approx(target)
    assert p['structure_risk_policy'] == FULL_SLOT_POLICY
    assert p['structure_risk_budget_usdt'] == pytest.approx(target*paper.MAX_POSITION_MARGIN_LOSS_RATIO)
    assert account.trades[0]['amount'] == pytest.approx(target, abs=1e-4)


def test_refreshed_funds_cannot_be_replaced_by_tiny_order(monkeypatch):
    account = SimpleNamespace(positions={}, pending_limit_orders={}, trades=[], logs=[], log=Mock(),
        get_wallet_balance=lambda: 200., get_available_balance=lambda: 200.,
        open_position=AsyncMock(return_value=True))
    async def refresh(**kwargs):
        account.get_available_balance = lambda: 20.
    account.refresh = AsyncMock(side_effect=refresh)
    engine, signal = make_engine(account, monkeypatch)
    assert not asyncio.run(engine._place_structured_entry_locked('CAP/USDT', signal, 101.5))
    account.open_position.assert_not_awaited()
    assert engine._entry_gate_diagnostics[('CAP/USDT', 'LONG', 'EXECUTION')][1] == 'BLOCKED_INSUFFICIENT_MARGIN_AT_SUBMIT'


def test_fresh_funds_can_recover_from_stale_insufficient_cache(monkeypatch):
    account = SimpleNamespace(positions={}, pending_limit_orders={}, trades=[], logs=[], log=Mock(),
        get_wallet_balance=lambda: 200., get_available_balance=lambda: 20.,
        open_position=AsyncMock(return_value=True))
    async def refresh(**kwargs):
        account.get_available_balance = lambda: 200.
    account.refresh = AsyncMock(side_effect=refresh)
    engine, signal = make_engine(account, monkeypatch)
    assert asyncio.run(engine._place_structured_entry_locked('CAP/USDT', signal, 101.5))
    assert account.open_position.await_args.kwargs['amount_usdt'] == pytest.approx(100./(1+5*config.TAKER_FEE_RATE))


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_testnet_order_quantity_keeps_full_margin_through_account_boundary(symbol, side, monkeypatch, tmp_path):
    from decimal import Decimal, ROUND_DOWN
    from core.testnet_account import BinanceTestnetAccount
    exchange = SimpleNamespace(
        create_order=AsyncMock(side_effect=AssertionError('No live exchange calls')),
        market=lambda symbol: dict(linear=True, contractSize=1., info={'filters': [
            dict(filterType=name, minQty='.001', maxQty='1000000', stepSize='.001')
            for name in ('LOT_SIZE', 'MARKET_LOT_SIZE')]}),
        amount_to_precision=lambda symbol, qty: str(Decimal(qty).quantize(Decimal('.001'), rounding=ROUND_DOWN)))
    account = BinanceTestnetAccount(exchange, state_file=str(tmp_path/'testnet.json'))
    account._ensure_markets = AsyncMock()
    account._prepare_leverage = AsyncMock()
    account._send_order = AsyncMock(return_value=dict(id='test-order', average=101.5 if side == 'LONG' else 98.5))
    account._finalize_new_position = AsyncMock(return_value=True)
    from test_entry_chop_gate import eligible_frame
    kind = 'pair' if symbol == 'CAP/USDT' else 'live'
    f = eligible_frame(kind, side)
    now = float(f.iloc[-1].timestamp)+1000
    monkeypatch.setattr('time.time', lambda: now/1000)
    f.attrs.update(entry_finality_verified=True, entry_finality_server_ms=now)
    account.entry_frame_provider = AsyncMock(return_value=f)
    context = dict(entry_mode='CHANNEL_SWING',
                   entry_signal_code=('KC_2BAR_CONFIRM_' if kind == 'pair' else 'KC_LIVE_BODY_BREAKOUT_')+side,
                   channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    margin = TradingEngine._half_wallet_entry_margin(200., 200., 5.)
    price = float(f.iloc[-1].close)
    assert asyncio.run(account.open_position(symbol, side, price, margin, 0., 0.,
                       'TEST', atr=1., leverage=5., entry_context=context))
    qty = account._send_order.await_args.args[3]
    assert 0 <= margin-qty*price/5. < .001*price/5.
    assert account._finalize_new_position.await_args.kwargs['entry_context']['structure_risk_policy'] == FULL_SLOT_POLICY
    assert account._finalize_new_position.await_args.args[13] == margin


def test_exchange_zero_available_balance_is_not_treated_as_full_wallet(tmp_path):
    from core.testnet_account import BinanceTestnetAccount
    exchange = SimpleNamespace(create_order=AsyncMock(),
        fapiPrivateV2GetBalance=AsyncMock(return_value=[
            dict(asset='USDT', balance='200', availableBalance='0')]),
        fapiPrivateV2GetPositionRisk=AsyncMock(return_value=[]))
    account = BinanceTestnetAccount(exchange, state_file=str(tmp_path/'testnet.json'))
    account._check_live_circuit_breaker = AsyncMock()
    account._fetch_exchange_order_snapshot = AsyncMock()
    asyncio.run(account.refresh(force=True))
    assert account.get_wallet_balance() == 200.
    assert account.get_available_balance() == 0.
    assert TradingEngine._half_wallet_entry_margin(200., account.get_available_balance(), 5.) == 0.


def test_confirmed_fill_budget_is_five_percent_of_actual_margin(tmp_path):
    from core.testnet_account import BinanceTestnetAccount, MAX_POSITION_MARGIN_LOSS_RATIO
    exchange = SimpleNamespace(create_order=AsyncMock(), price_to_precision=lambda s, p: str(p))
    account = BinanceTestnetAccount(exchange, state_file=str(tmp_path/'testnet.json'))
    account._cancel_all_orders = AsyncMock()
    account._create_protection_order = AsyncMock()
    account.refresh = AsyncMock()
    account.save_state = Mock()
    assert asyncio.run(account._finalize_new_position(
        'CAP/USDT', 'LONG', 101.5, 4.913, 101.5, 99., 0., 'TEST', 1., 5, None,
        'sell', 'test-order', 99.750623,
        entry_context=dict(entry_mode='CHANNEL_SWING', structure_risk_policy=FULL_SLOT_POLICY,
                           structure_risk_budget_usdt=99.750623*.05)))
    actual_margin = 101.5*4.913/5.
    assert account.position_meta['CAP/USDT']['structure_risk_budget_usdt'] == pytest.approx(actual_margin*MAX_POSITION_MARGIN_LOSS_RATIO)
    assert account.trades[0]['structure_risk_budget_usdt'] == pytest.approx(actual_margin*MAX_POSITION_MARGIN_LOSS_RATIO)
