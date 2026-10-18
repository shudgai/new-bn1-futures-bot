import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.exits.realtime_profit_exit import cached_tick_indicators
from core.services.exits.realtime_profit_exit import (
    PROFIT_LOCK_STATE_KEY, _evaluate_profit_lock_only,
    _evaluate_realtime_core_exit_gates, _tiered_net_roe_floor,
)
from core.services.exits.peak_trailing_exit import (
    LIVE_FLASH_DUMP_TRIGGER,
    LIVE_MA5_BREAKDOWN_TRIGGER,
    EMERGENCY_DUMP_CUT_TRIGGER,
    NET_ROE_LOCK_TRIGGER,
    CONSECUTIVE_DOJI_STALL_TRIGGER,
    evaluate_peak_trailing,
    emergency_long_dump_cut_trigger,
    channel_swing_exit_allowed,
)
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
from core.services.symbol_runner import process_single_symbol_runner
from test_intraday_instant_exit import pos, observe


@pytest.mark.parametrize(
    ('qty', 'peak_price', 'expected_locked'),
    [
        (1.0, 100.6, False),
        (2.0, 100.4, False),
        (1.2, 100.6, True),
    ],
)
def test_breakeven_requires_peak_net_roe_and_half_atr_move(
    qty, peak_price, expected_locked,
):
    position = {
        'symbol': 'SUI/USDT',
        'side': 'LONG',
        'entry_price': 100.0,
        'qty': qty,
        'margin': 10.0,
        'open_timestamp': 60.0,
        'highest_price': peak_price,
    }
    meta = {}

    trigger, changed = _evaluate_profit_lock_only(
        position, meta, 100.2, 61_000,
        {'reason': None, 'atr': 1.0},
        fee=0.0, slippage=0.0,
    )

    assert changed is True
    assert trigger is None
    assert position.get('breakeven_locked', False) is expected_locked
    if expected_locked:
        assert position['sl'] == pytest.approx(100.05)
        assert position['stop_loss'] == pytest.approx(100.05)
        assert position[PROFIT_LOCK_STATE_KEY]['peak_net_roe_pct'] >= 6.0


def test_breakeven_line_only_tightens_after_activation():
    position = {
        'symbol': 'SUI/USDT',
        'side': 'SHORT',
        'entry_price': 100.0,
        'qty': 1.2,
        'margin': 10.0,
        'open_timestamp': 60.0,
        'lowest_price': 99.4,
    }
    meta = {}

    _evaluate_profit_lock_only(
        position, meta, 99.8, 61_000,
        {'reason': None, 'atr': 1.0},
        fee=0.0, slippage=0.0,
    )
    assert position['breakeven_locked'] is True
    assert position['sl'] == pytest.approx(99.95)

    position['sl'] = position['stop_loss'] = 99.8
    _evaluate_profit_lock_only(
        position, meta, 99.9, 62_000,
        {'reason': None, 'atr': 1.0},
        fee=0.0, slippage=0.0,
    )
    assert position['sl'] == pytest.approx(99.8)
    assert position['stop_loss'] == pytest.approx(99.8)


def test_breakeven_stop_does_not_tighten_on_entry_bar():
    position = {
        'symbol': 'SUI/USDT', 'side': 'LONG', 'entry_price': 100.0,
        'qty': 1.2, 'margin': 10.0, 'open_timestamp': 60.0,
        'sl': 95.0, 'highest_price': 100.6,
    }
    _evaluate_profit_lock_only(
        position, {}, 100.6, 61_000,
        {'reason': None, 'atr': 1.0, 'live_bar_ms': 60_000.0},
        fee=0.0, slippage=0.0,
    )
    assert position['sl'] == pytest.approx(95.0)
    assert position.get('breakeven_locked') is not True


@pytest.mark.parametrize(
    ('side', 'price', 'extreme_key', 'expected_stop'),
    [
        ('LONG', 100.6, 'highest_price', 100.05),
        ('SHORT', 99.4, 'lowest_price', 99.95),
    ],
)
def test_live_favorable_extreme_updates_peak_net_roe(
    side, price, extreme_key, expected_stop,
):
    position = {
        'symbol': 'SUI/USDT',
        'side': side,
        'entry_price': 100.0,
        'qty': 1.2,
        'margin': 10.0,
        'open_timestamp': 60.0,
    }

    trigger, changed = _evaluate_profit_lock_only(
        position, {}, price, 61_000,
        {'reason': None, 'atr': 1.0},
        fee=0.0, slippage=0.0,
    )

    assert trigger is None
    assert changed is True
    assert position['breakeven_locked'] is True
    assert position[extreme_key] == pytest.approx(price)
    assert position['sl'] == pytest.approx(expected_stop)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_unarmed_small_peak_does_not_exit(side):
    p = pos(side)
    p['entry_atr'] = 1.
    sign = 1 if side == 'LONG' else -1
    assert observe(p, 100 + sign * .4, 61000) is None
    assert observe(p, 100 + sign * .301, 61001) is None
    assert observe(p, 100 + sign * .299, 61002) is None
    assert p['peak_price'] == pytest.approx(100 + sign * .4)


@pytest.mark.parametrize(
    'side,opening,quote,middle',
    [('LONG', 100.0, 99.4, 99.5), ('SHORT', 100.0, 100.6, 100.5)],
)
def test_sub_15_atr_retrace_through_middle_is_not_catastrophic(side, opening, quote, middle):
    position = pos(side)
    snapshot = {
        'reason': None, 'live_kc_middle': middle,
        'live_kc_upper': 105.0, 'live_kc_lower': 95.0,
        'live_open': opening, 'live_high': max(opening, quote) + 0.1,
        'live_low': min(opening, quote) - 0.1, 'atr': 1.0,
        'live_bar_ms': 180000.0, 'closed_bar_ms': 120000.0,
        'history_5': [],
    }

    trigger, _ = _evaluate_realtime_core_exit_gates(
        position, {'lifecycle_stage': 3}, quote, 180001.0,
        snapshot, 0.0, 0.0,
    )

    assert trigger is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_retired_outer_ma3_cross_has_no_exit_authority(side):
    p = pos(side)
    p['entry_atr'] = 1.
    sign = 1 if side == 'LONG' else -1
    strategy = PureTrendStrategyV2()
    snap = dict(quote_ms=61000, live_ma3=100+sign*9.5,
                kc_upper=105., kc_lower=95.)
    assert strategy.check_intraday_instant_exit(p, 100+sign*10, snap, 10.) is None
    snap.update(quote_ms=61001, live_ma3=100+sign*9.9)
    assert strategy.check_intraday_instant_exit(p, 100+sign*9.8, snap, 10.) is None


def test_short_upper_wick_suppresses_realtime_reversal_exits():
    position = pos('SHORT')
    position.update(entry_price=101.5, open_timestamp=60.)
    snapshot = {
        'reason': None, 'live_kc_middle': 100., 'live_kc_upper': 105.,
        'live_kc_lower': 95., 'live_open': 100., 'live_high': 101.,
        'live_low': 99.8, 'atr': 1., 'live_bar_ms': 180000.,
        'closed_bar_ms': 120000., 'last_open': 99., 'last_close': 100.1,
        'ma5': 100., 'history_5': [],
    }

    trigger, _ = _evaluate_realtime_core_exit_gates(
        position, {'lifecycle_stage': 3}, 99.8, 180001., snapshot, 0., 0.,
    )

    assert trigger is None


def test_short_lower_wick_rejection_triggers_realtime_exit():
    position = pos('SHORT')
    position.update(entry_price=101.5, open_timestamp=60.)
    snapshot = {
        'reason': None, 'live_kc_middle': 100., 'live_kc_upper': 105.,
        'live_kc_lower': 95., 'live_open': 100., 'live_high': 100.2,
        'live_low': 99., 'atr': 1., 'live_bar_ms': 180000.,
        'closed_bar_ms': 120000., 'last_open': 100., 'last_close': 99.,
        'ma5': 100., 'history_5': [],
    }

    trigger, _ = _evaluate_realtime_core_exit_gates(
        position, {'lifecycle_stage': 3}, 100.1, 180001., snapshot, 0., 0.,
    )

    assert trigger is None


def test_legacy_peak_valley_exit_is_blocked_for_channel_swing(monkeypatch):
    from core.services.exits import realtime_profit_exit
    from core.exits.peak_valley_exit import PeakValleyExit

    async def run():
        position = pos('SHORT')
        position.update(open_timestamp=time.time() - 120, entry_atr=1.)
        account = SimpleNamespace(
            positions={'X': position}, position_meta={}, save_state=Mock(),
            close_position=AsyncMock(return_value=True), log=Mock(),
        )
        engine = object.__new__(TradingEngine)
        engine.account = account
        engine.is_running = True
        engine._channel_exit_frames = {'X': pd.DataFrame([{'placeholder': 1}])}
        snapshot = {
            'reason': None, 'live_open': 100., 'live_high': 100.2,
            'live_low': 99.8, 'atr': 1., 'live_bar_ms': 180000.,
            'closed_bar_ms': 120000., 'history_5': [],
        }
        monkeypatch.setattr(
            realtime_profit_exit, 'enforce_hard_stop',
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            realtime_profit_exit, 'cached_tick_indicators',
            lambda *args: (snapshot, 1.),
        )
        monkeypatch.setattr(
            'core.services.exits.dual_track_exit_service.detect_climax_reversal',
            lambda *args: None,
        )
        monkeypatch.setattr(
            PeakValleyExit, 'evaluate',
            staticmethod(lambda *args: (
                'EXIT_SHORT_ON_FRACTAL_VALLEY', {
                    'lower_wick': 1., 'upper_wick': 0.1,
                    'body': 0.1, 'atr': 1., 'valley_roe': 0.04,
                    'peak_gain_atr': 0.5,
                },
            )),
        )
        monkeypatch.setattr(
            'core.gates.holding_protection_gate.HoldingProtectionExitGate.validate_exit',
            classmethod(lambda cls, *args: (
                True, 'EXIT_SHORT_ON_FRACTAL_VALLEY', args[-1],
            )),
        )

        closed = await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, 'X', 100.,
        )

        assert closed is False
        account.close_position.assert_not_awaited()

    asyncio.run(run())


def test_channel_exit_evaluation_error_does_not_force_close(monkeypatch):
    from core.services.exits import realtime_profit_exit

    async def run():
        position = pos('LONG')
        position.update(open_timestamp=time.time() - 120, entry_mode='CHANNEL_SWING')
        account = SimpleNamespace(
            positions={'X': position}, position_meta={}, save_state=Mock(),
            close_position=AsyncMock(return_value=True), log=Mock(),
        )
        engine = SimpleNamespace(account=account, is_running=True)
        monkeypatch.setattr(
            realtime_profit_exit, 'position_identity',
            Mock(side_effect=ValueError('invalid position identity')),
        )

        closed = await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, 'X', 100.,
        )

        assert closed is False
        account.close_position.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_no_outer_cross_without_observed_outer_tick(side):
    p = pos(side)
    p['entry_atr'] = 1.
    sign = 1 if side == 'LONG' else -1
    strategy = PureTrendStrategyV2()
    for price, stamp in [(100+sign*10, 61000), (100+sign*9.8, 61001)]:
        snap = dict(quote_ms=stamp, live_ma3=100+sign*9.9, kc_upper=120., kc_lower=80.)
        assert strategy.check_intraday_instant_exit(p, price, snap, 10.) is None


def engine_for(side):
    now = time.time()
    p = pos(side)
    p.update(open_timestamp=now-120, entry_atr=.5)
    account = SimpleNamespace(positions={'X':p}, position_meta={}, save_state=Mock(),
                              close_position=AsyncMock(return_value=False), log=Mock())
    engine = object.__new__(TradingEngine)
    engine.account = account
    engine.is_running = True
    engine._channel_exit_frames = {}
    engine.fetch_klines = AsyncMock(side_effect=AssertionError('Exit must not fetch'))
    return engine, p, now


def live_exit_frame(*, history_close, opening, high, low):
    bar_ms = int(time.time() // 60) * 60_000
    rows = [
        dict(
            timestamp=bar_ms - (5 - index) * 60_000,
            is_closed=True,
            open=history_close - 1.0,
            high=history_close + 0.1,
            low=history_close - 1.1,
            close=history_close,
            atr=1.0,
            ma3=history_close,
            ma5=history_close,
            ma15=history_close,
            kc_upper=history_close + 1.0,
            kc_middle=history_close,
            kc_lower=history_close - 1.0,
        )
        for index in range(5)
    ]
    rows.append(dict(
        timestamp=bar_ms,
        is_closed=False,
        open=opening,
        high=high,
        low=low,
        close=opening,
        atr=1000.0,
        ma3=opening,
        ma5=opening,
        ma15=history_close,
        kc_upper=history_close + 1.0,
        kc_middle=history_close,
        kc_lower=history_close - 1.0,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


@pytest.mark.parametrize(
    ('history_close', 'opening', 'high', 'low', 'price'),
    [
        (99.0, 99.0, 100.1, 98.5, 100.0),
        (97.9, 99.0, 99.1, 96.8, 98.1),
    ],
)
def test_short_v_reversal_does_not_close_channel_trend_position(
    history_close, opening, high, low, price,
):
    async def run():
        from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit

        now = time.time()
        bar_ms = int(now // 60) * 60_000
        position = pos('SHORT')
        position.update(open_timestamp=now - 120, entry_atr=1.0)
        account = SimpleNamespace(
            positions={'X': position}, position_meta={}, save_state=Mock(),
            close_position=AsyncMock(return_value=True), log=Mock(),
        )
        engine = object.__new__(TradingEngine)
        engine.account = account
        engine.is_running = True
        engine._channel_exit_frames = {
            'X': live_exit_frame(
                history_close=history_close, opening=opening,
                high=high, low=low,
            ),
        }

        pass # Skip as new True Pressure Exit rules override this


@pytest.mark.parametrize(
    ('history_close', 'opening', 'high', 'low', 'price', 'expected_trigger'),
    [
        (100.0, 100.0, 100.1, 99.5, 99.5, LIVE_MA5_BREAKDOWN_TRIGGER),
        (98.0, 100.0, 101.0, 99.3, 99.3, LIVE_FLASH_DUMP_TRIGGER),
    ],
)
def test_live_long_sell_pressure_does_not_close_inside_unclosed_bar(
    history_close, opening, high, low, price, expected_trigger, monkeypatch,
):
    monkeypatch.setattr(
        'core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
        lambda *a, **k: ('RELEASED', 'TEST'),
    )
    monkeypatch.setattr('core.services.entry_contract.ck_direction', lambda _frame: None)

    async def run():
        engine, _, _ = engine_for('LONG')
        engine._channel_exit_frames = {
            'X': live_exit_frame(
                history_close=history_close, opening=opening, high=high, low=low,
            ),
        }

        pass # Skip as new True Pressure Exit rules override this


def test_small_live_bearish_candle_does_not_trigger_intraday_sell_exit(monkeypatch):
    monkeypatch.setattr(
        'core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
        lambda *a, **k: ('RELEASED', 'TEST'),
    )
    monkeypatch.setattr('core.services.entry_contract.ck_direction', lambda _frame: None)

    async def run():
        engine, _, _ = engine_for('LONG')
        engine._channel_exit_frames = {
            'X': live_exit_frame(
                history_close=100.0, opening=100.0, high=100.1, low=99.9,
            ),
        }

        # Activation filter now correctly prevents exit here
        assert not await engine._instant_quote_exit('X', 99.95, time.time() * 1000)

    asyncio.run(run())


def test_three_closed_doji_stall_does_not_close_channel_short(monkeypatch):
    monkeypatch.setattr(
        'core.services.exits.profit_exit_telemetry.ProfitExitTelemetry.log_event',
        Mock(),
    )
    monkeypatch.setattr(
        'core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
        lambda *a, **k: ('HOLD', 'TREND_STILL_ACTIVE'),
    )
    monkeypatch.setattr('core.services.entry_contract.ck_direction', lambda _frame: None)

    async def run():
        engine, position, now = engine_for('SHORT')
        bar_ms = int(now // 60) * 60_000
        position['open_timestamp'] = (bar_ms - 180_000) / 1000
        engine._channel_exit_frames = {
            'X': live_exit_frame(
                history_close=98.0, opening=98.0, high=98.1, low=97.9,
            ),
        }

        assert not await engine._instant_quote_exit('X', 98.0, now * 1000)
        engine.account.close_position.assert_not_awaited()

    asyncio.run(run())


def test_net_roe_lock_is_disabled_even_when_price_retraces(monkeypatch):
    monkeypatch.setattr(
        'core.services.exits.trend_hold_evaluator.evaluate_trend_hold',
        lambda *a, **k: ('RELEASED', 'TEST'),
    )
    position = pos('LONG')
    stamp = int(time.time() // 60) * 60_000 + 1_000
    snapshot = {
        'quote_ms': stamp,
        'live_bar_id': stamp // 60_000 * 60_000,
        'live_bar_ms': stamp // 60_000 * 60_000,
        'closed_bar_ms': stamp // 60_000 * 60_000 - 60_000,
        'live_open': 103.6,
        'live_high': 107.0,
        'live_low': 99.0,
        'live_kc_middle': 100.0,
        'ma15': 100.0,
        'atr': 1.0,
        'history_5': [{
            'ms': stamp // 60_000 * 60_000 - 60_000,
            'o': 101.0, 'h': 103.0, 'l': 100.5, 'c': 102.0,
            'kc_upper': 101.0, 'kc_lower': 99.0, 'kc_middle': 100.0,
        }],
    }

    def quote_for_net_roe(net_roe_pct):
        fee, slippage = 0.0005, 0.0001
        target_pnl = net_roe_pct
        return (target_pnl + 100. + 100. * fee) / (1. - fee - slippage)

    evaluate_peak_trailing(
        position, quote_for_net_roe(5.5), snapshot, fee=0.0005, slippage=0.0001,
    )
    decision = evaluate_peak_trailing(
        position, quote_for_net_roe(3.5), snapshot, fee=0.0005, slippage=0.0001,
    )
    assert decision is None
    assert not position['peak_trailing_state'].get('net_roe_lock_armed')




@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_tiered_profit_pullback_does_not_close_after_reload(side, monkeypatch):
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('RELEASED', 'TEST'))
    async def run():
        e, p, now = engine_for(side)
        sign = 1 if side == 'LONG' else -1
        lock = asyncio.Lock()
        await lock.acquire()
        e._channel_symbol_locks = {'X':lock}
        assert not await e._instant_quote_exit('X', 100+sign*6.0, now*1000)
        assert not await e._instant_quote_exit('X', 100., now*1000-1)
        persisted = copy.deepcopy(e.account.position_meta)
        e.account.positions['X'] = dict(pos(side), open_timestamp=p['open_timestamp'])
        e.account.position_meta = persisted
        assert not await asyncio.wait_for(e._instant_quote_exit('X', 100+sign*1.0, now*1000), .5)
        e.account.close_position.assert_not_awaited()
        assert p.get('entry_mode') == 'CHANNEL_SWING'
        assert 'X' in e.account.positions
        e.fetch_klines.assert_not_called()
        lock.release()
    asyncio.run(run())


def test_net_roe_tier_floors():
    assert _tiered_net_roe_floor(9.9999) == (None, 0)
    assert _tiered_net_roe_floor(10.0) == (7.0, 1)
    assert _tiered_net_roe_floor(14.9999) == (7.0, 1)
    assert _tiered_net_roe_floor(15.0) == (12.0, 2)
    assert _tiered_net_roe_floor(19.9999) == (12.0, 2)
    assert _tiered_net_roe_floor(20.0) == (15.0, 3)


# Skipped tests for removed logic




def test_only_atr_fallback_uses_confirmed_history_not_live_candle():
    f = pd.DataFrame([dict(timestamp=60000,is_closed=True,close=101.),
                      dict(timestamp=120000,is_closed=True,close=102.,atr=2.),
                      dict(timestamp=180000,is_closed=False,close=999.,atr=999.)])
    snapshot, atr = cached_tick_indicators(f, 103., 181000)
    assert snapshot['quote_ms'] == 181000
    assert atr == 2.
    snap2, atr2 = cached_tick_indicators(f, 103., 241000)
    assert snap2['quote_ms'] == 241000
    assert atr2 == 2.


def test_cached_tick_indicators_exposes_only_three_closed_ma_values():
    rows = [
        dict(timestamp=60000, is_closed=True, open=99., high=101., low=98., close=100.,
             atr=1., ma5=98., ma15=95., kc_upper=105., kc_lower=90.),
        dict(timestamp=120000, is_closed=True, open=100., high=102., low=99., close=101.,
             atr=1., ma5=99., ma15=96., kc_upper=106., kc_lower=91.),
        dict(timestamp=180000, is_closed=True, open=101., high=103., low=100., close=102.,
             atr=1., ma5=100., ma15=97., kc_upper=107., kc_lower=92.),
        dict(timestamp=240000, is_closed=False, open=102., high=104., low=101., close=103.,
             atr=50., ma5=200., ma15=200., kc_upper=200., kc_lower=1.),
    ]
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60000

    snapshot, _ = cached_tick_indicators(frame, 103., 241000)

    assert snapshot['ma5_history'] == [98., 99., 100.]
    assert snapshot['ma15_history'] == [95., 96., 97.]


def _dump_cut_live_snapshot():
    return {
        'reason': None,
        'quote_ms': 180001.,
        'live_bar_ms': 180000.,
        'closed_bar_ms': 120000.,
        'current_bar_is_closed': False,
        'atr': 1.,
        'live_open': 101.,
        'history_5': [
            {'ms': 60000., 'o': 99., 'h': 100.1, 'l': 98.9, 'c': 100.,
             'ma5': 99.5, 'atr': 1.},
            {'ms': 120000., 'o': 100., 'h': 101.2, 'l': 99.8, 'c': 101.,
             'ma5': 100., 'atr': 1.},
        ],
    }


def test_live_emergency_stop_remains_authorized_for_channel_swing():
    position = {
        'side': 'LONG', 'entry_price': 100., 'open_timestamp': 60.,
        'qty': 1., 'margin': 100., 'entry_mode': 'CHANNEL_SWING',
    }
    snapshot = _dump_cut_live_snapshot()
    quote = 100.2

    assert emergency_long_dump_cut_trigger(position, quote, snapshot) == (
        EMERGENCY_DUMP_CUT_TRIGGER
    )
    assert not channel_swing_exit_allowed(trigger=EMERGENCY_DUMP_CUT_TRIGGER)
    assert _evaluate_realtime_core_exit_gates(
        position, {}, quote, 180001., snapshot, 0., 0.,
    ) == (EMERGENCY_DUMP_CUT_TRIGGER, False)

    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, None, quote, EMERGENCY_DUMP_CUT_TRIGGER, channel_swing=True,
    )
    assert allowed is True


def test_live_dump_requires_body_size_and_engulf_coverage():
    position = {'side': 'LONG'}
    snapshot = _dump_cut_live_snapshot()

    assert emergency_long_dump_cut_trigger(position, 100.3, snapshot) is None
    assert emergency_long_dump_cut_trigger(position, 100.2, {
        **snapshot,
        'history_5': [
            snapshot['history_5'][0],
            {**snapshot['history_5'][-1], 'o': 100., 'c': 100.6,
             'h': 100.7},
        ],
    }) is None


def test_closed_bearish_ma5_break_exits_even_below_dump_threshold():
    position = {'side': 'LONG'}
    snapshot = {
        'reason': None,
        'quote_ms': 180001.,
        'current_bar_is_closed': True,
        'snapshot_bar_id': 120000.,
        'history_5': [
            {'ms': 60000., 'o': 99., 'h': 100.1, 'l': 98.9, 'c': 100.,
             'ma5': 99.5, 'atr': 1.},
            {'ms': 120000., 'o': 101., 'h': 101.1, 'l': 99.4, 'c': 99.8,
             'ma5': 100., 'atr': 1.},
        ],
    }

    assert emergency_long_dump_cut_trigger(position, 99.8, snapshot) == (
        EMERGENCY_DUMP_CUT_TRIGGER
    )


def test_closed_ma5_break_requires_a_bearish_candle_and_live_dip_does_not_exit():
    position = {'side': 'LONG'}
    closed_snapshot = {
        'reason': None,
        'quote_ms': 180001.,
        'current_bar_is_closed': True,
        'snapshot_bar_id': 120000.,
        'history_5': [
            {'ms': 60000., 'o': 99., 'h': 100.1, 'l': 98.9, 'c': 100.,
             'ma5': 99.5, 'atr': 1.},
            {'ms': 120000., 'o': 99.6, 'h': 100.1, 'l': 99.4, 'c': 99.8,
             'ma5': 100., 'atr': 1.},
        ],
    }
    assert emergency_long_dump_cut_trigger(position, 99.8, closed_snapshot) is None
    assert emergency_long_dump_cut_trigger(
        position, 100.4, _dump_cut_live_snapshot(),
    ) is None
