"""Symmetric slope gates for all A-E entries and every active exit retry."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_strict_entry_firewall import frame
from core.services.entry_firewall import validate_entry_frame
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('rule', list('ABCDE'))
@pytest.mark.parametrize('fault', ['flat', 'adverse', 'ma15_equal', 'ma15_wrong'])
def test_no_rule_bypasses_slope_and_alignment(side, rule, fault):
    f = frame('B', side)
    sign = 1 if side == 'LONG' else -1
    if fault == 'flat': f.loc[2, 'ma3'] = f.loc[1, 'ma3']
    if fault == 'adverse': f.loc[2, 'ma3'] = f.loc[1, 'ma3'] - sign*.1
    if fault == 'ma15_equal': f.loc[2, 'ma15'] = f.loc[2, 'ma3']
    if fault == 'ma15_wrong': f.loc[2, 'ma15'] = f.loc[2, 'ma3'] + sign*.1
    assert not evaluate_closed_entry(f, side)[0]
    with pytest.raises(ValueError, match='MA3'):
        validate_entry_frame(f, side, f'CLOSED_{rule}_{side}')


def exit_case(side):
    f = frame('B')
    f['high'], f['low'] = 130., 70.
    f['kc_upper'], f['kc_middle'], f['kc_lower'] = 112., 105., 98.
    f.loc[1, ['open', 'close', 'ma3']] = [111., 112., 111.]
    f.loc[2, ['open', 'close', 'ma3']] = [110., 109., 110.]
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a] = 200-original[b]
    p = dict(side=side, entry_price=100., open_timestamp=1., qty=1., margin=100., is_half_closed=True)
    return f,p


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('slope', ['favorable', 'flat'])
@pytest.mark.parametrize('pending', [None, 'TP1_PARTIAL_CLOSE_50PCT', 'EXIT_KC_MIDDLE_CLOSED',
                                    'EXIT_ATR_TRAIL_CLOSED', 'EXIT_OUTER_ENGULFING_CLOSED',
                                    'EXIT_OUTER_MA3_CURVE_CLOSED'])
def test_no_active_exit_while_ma3_favorable_even_with_pending(side, slope, pending):
    f,p = exit_case(side)
    strategy = DualTrackExitStrategy()
    strategy.evaluate_exit(p, f)
    p['closed_exit_state']['pending'] = pending
    sign = 1 if side == 'LONG' else -1
    f.loc[2,'ma3'] = f.loc[1,'ma3'] + (sign if slope == 'favorable' else 0.)
    p['qty'] = 100.  # Large profit must not trigger a partial TP either.
    assert strategy.evaluate_exit(p, f) is None
    assert p['closed_exit_state']['pending'] is None
    restored = copy.deepcopy(p)
    assert strategy.evaluate_exit(restored, f) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('fault', ['no_cross', 'equal_cross', 'no_profit', 'invalid_ma'])
def test_exit_requires_every_condition(side, fault):
    f,p = exit_case(side)
    sign = 1 if side == 'LONG' else -1
    if fault == 'no_cross': f.loc[2,'close'] = f.loc[2,'ma3'] + sign*.1
    if fault == 'equal_cross': f.loc[2,'close'] = f.loc[2,'ma3']
    if fault == 'no_profit': p['entry_price'] = float(f.loc[2,'close']) - sign*.1
    if fault == 'invalid_ma': f.loc[2,'ma3'] = float('nan')
    assert DualTrackExitStrategy().evaluate_exit(p,f) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_valid_exit_retries_then_cancels_when_direction_recovers(side):
    f,p = exit_case(side)
    strategy = DualTrackExitStrategy()
    expected = 'EXIT_OUTER_MA3_CURVE_CLOSED'
    assert strategy.evaluate_exit(p,f) == expected
    assert strategy.evaluate_exit(copy.deepcopy(p),f) == expected
    f.loc[2,'ma3'] = f.loc[1,'ma3']
    assert strategy.evaluate_exit(p,f) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_exchange_never_receives_adverse_ma3_entry(side):
    from core.testnet_account import BinanceTestnetAccount
    f = frame('B',side)
    f.loc[2,'ma3'] = f.loc[1,'ma3']
    account = object.__new__(BinanceTestnetAccount)
    account.exchange = SimpleNamespace(create_order=AsyncMock())
    account.entry_frame_provider = AsyncMock(return_value=f)
    account.last_closed_at = {}
    with pytest.raises(ValueError,match='MA3'):
        asyncio.run(account._send_order('TEST','market','buy' if side=='LONG' else 'sell',1,
            entry_context=dict(entry_signal_code=f'CLOSED_B_{side}',
                               channel_confirmation_bar_id=float(f.iloc[-1].timestamp))))
    account.exchange.create_order.assert_not_awaited()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_roe_uses_margin_not_price_move(side, monkeypatch):
    import core.services.exits.dual_track_exit_service as m
    monkeypatch.setattr(m, 'TAKER_FEE_RATE', 0.)
    monkeypatch.setattr(m, 'SLIPPAGE_PCT', 0.)
    f,p = exit_case(side)
    sign = 1 if side == 'LONG' else -1
    f.loc[2,'close'] = 100.+sign*.5
    f.loc[2,'open'] = 100.+sign*.6
    f.loc[2,'ma3'] = 100.+sign*.8
    f.loc[1,'ma3'] = 100.+sign*1.
    f['kc_middle'] = 100.-sign*1.
    p.update(margin=5., is_half_closed=False)
    assert m.DualTrackExitStrategy().evaluate_exit(p,f) == 'EXIT_OUTER_MA3_CURVE_CLOSED'
    assert p['closed_exit_state']['margin_roe'] == pytest.approx(.1)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('trigger', ['roe', 'usdt'])
def test_tp1_thresholds_are_independent_of_ma3(side, trigger, monkeypatch):
    import core.services.exits.dual_track_exit_service as m
    monkeypatch.setattr(m, 'TAKER_FEE_RATE', 0.)
    monkeypatch.setattr(m, 'SLIPPAGE_PCT', 0.)
    f,p = exit_case(side)
    p.update(is_half_closed=False, margin=60. if trigger=='roe' else 1000.)
    sign = 1 if side == 'LONG' else -1
    f.loc[2,'ma3'] = f.loc[1,'ma3'] + sign
    if trigger=='roe': p['qty'] = .1; p['margin'] = 6.
    assert m.DualTrackExitStrategy().evaluate_exit(p,f) == 'TP1_PARTIAL_CLOSE_50PCT'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['atr', 'middle'])
def test_hard_stops_bypass_favorable_ma3(side, kind):
    f,p = exit_case(side)
    p.update(is_half_closed=False, entry_atr=1.)
    sign = 1 if side == 'LONG' else -1
    f.loc[2,'ma3'] = f.loc[1,'ma3'] + sign
    if kind=='atr':
        f.loc[2,'close'] = 100.-sign*1.5
        f['kc_middle'] = 100.-sign*2.
        f['kc_lower' if side=='LONG' else 'kc_upper'] = 100.-sign*5.
    else:
        f.loc[2,'close'] = 100.+sign*4.
    expected = 'EXIT_INITIAL_ATR_HARD_STOP' if kind=='atr' else 'EXIT_KC_MIDDLE_HARD_STOP'
    assert DualTrackExitStrategy().evaluate_exit(p,f) == expected


def test_half_wallet_sizing_respects_fee_capacity():
    from core.engine import TradingEngine
    assert TradingEngine._half_wallet_entry_margin(200.,200.,10.) == 100.
    assert TradingEngine._half_wallet_entry_margin(200.,150.,10.) == 100.
    assert TradingEngine._half_wallet_entry_margin(200.,50.,10.) < 50.
    assert TradingEngine._half_wallet_entry_margin(float('nan'),100.,10.) == 0.


@pytest.mark.parametrize('filled', [True, False])
def test_tp1_runner_moves_remaining_stop_only_after_fill(filled):
    from core.services.symbol_runner import process_single_symbol_runner
    f,p = exit_case('LONG')
    p.update(is_half_closed=False, margin=100.)
    account = SimpleNamespace(positions={'TEST':p}, position_meta={}, save_state=lambda:None,
                              log=lambda *args:None)
    async def partial(*args, **kwargs):
        assert kwargs['fraction'] == .5
        if filled:
            p['qty'] *= .5
            p['margin'] *= .5
        return filled
    account.partial_close_position = AsyncMock(side_effect=partial)
    account.close_position = AsyncMock()
    engine = SimpleNamespace(account=account, _take_over_manual_position=lambda *args:None)
    asyncio.run(process_single_symbol_runner(engine,'TEST',0,None,False,exit_frame=f,exit_quote=109.))
    account.partial_close_position.assert_awaited_once()
    account.close_position.assert_not_awaited()
    if filled:
        assert p['qty'] == .5 and p['margin'] == 50.
        assert p['sl'] == p['atr_sl'] == p['entry_price']
        assert account.position_meta['TEST']['closed_exit_state']['tp1_executed'] is True
        assert p['closed_exit_state']['pending'] is None
    else:
        assert p['qty'] == 1.
        assert not p['closed_exit_state']['tp1_executed']
        assert p['closed_exit_state']['pending'] == 'TP1_PARTIAL_CLOSE_50PCT'


@pytest.mark.parametrize('symbol', ['1000PEPE/USDT', 'BTC/USDT'])
def test_actual_order_uses_half_wallet(monkeypatch, symbol):
    import core.engine as module
    monkeypatch.setattr(module,'DEFAULT_SYMBOLS',[symbol])
    f=frame('B')
    decision=evaluate_closed_entry(f,'LONG')[2]
    account=SimpleNamespace(positions={},pending_limit_orders={},trades=[],
        get_wallet_balance=lambda:200.,get_available_balance=lambda:200.,
        open_position=AsyncMock(return_value=True))
    engine=object.__new__(module.TradingEngine)
    engine.account=account
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:10.)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    engine._fresh_channel_entry_snapshot=AsyncMock(return_value=dict(frame=f,decision=decision,price=101.5))
    engine.tickers={symbol:101.5}
    signal=dict(side='LONG',entry_mode='CHANNEL_SWING',signal_code='CLOSED_B_LONG',
                candidate_bar_id=decision['confirmation_bar_id'],score=100,size_fraction=.1)
    assert asyncio.run(engine._place_structured_entry_locked(symbol,signal,101.5))
    assert account.open_position.await_args.kwargs['amount_usdt'] == 100.
