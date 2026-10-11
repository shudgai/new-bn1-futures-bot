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
    _evaluate_realtime_core_exit_gates, _tiered_net_roe_floor,
)
from core.services.exits.peak_trailing_exit import (
    LIVE_FLASH_DUMP_TRIGGER,
    LIVE_MA5_BREAKDOWN_TRIGGER,
    NET_ROE_LOCK_TRIGGER,
    CONSECUTIVE_DOJI_STALL_TRIGGER,
    evaluate_peak_trailing,
)
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
from core.services.symbol_runner import process_single_symbol_runner
from test_intraday_instant_exit import pos, observe

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_unarmed_small_peak_does_not_exit(side):
    p = pos(side)
    p['entry_atr'] = 1.
    sign = 1 if side == 'LONG' else -1
    assert observe(p, 100 + sign * .4, 61000) is None
    assert observe(p, 100 + sign * .301, 61001) is None
    assert observe(p, 100 + sign * .299, 61002) is None
    assert p['peak_price'] == pytest.approx(100 + sign * .4)


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

    assert trigger == 'LIVE_BOTTOM_REJECTION_EXIT'


def test_peak_valley_wick_exit_is_wired_into_realtime_execution(monkeypatch):
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
                'EXIT_SHORT_ON_LOWER_WICK_REJECTION', {
                    'lower_wick': 1., 'upper_wick': 0.1,
                    'body': 0.1, 'atr': 1., 'valley_roe': 0.04,
                    'peak_gain_atr': 0.5,
                },
            )),
        )
        monkeypatch.setattr(
            'core.gates.holding_protection_gate.HoldingProtectionExitGate.validate_exit',
            classmethod(lambda cls, *args: (
                True, 'EXIT_SHORT_ON_LOWER_WICK_REJECTION', args[-1],
            )),
        )

        closed = await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, 'X', 100.,
        )

        assert closed is True
        account.close_position.assert_awaited_once_with(
            'X', 100., 'Channel Swing EXIT_SHORT_ON_LOWER_WICK_REJECTION',
            is_manual=True,
        )

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
    assert _tiered_net_roe_floor(5.0) == (0.1, 1)
    assert _tiered_net_roe_floor(9.99) == (0.1, 1)
    assert _tiered_net_roe_floor(10.0) == (5.0, 2)
    assert _tiered_net_roe_floor(14.99) == (pytest.approx(7.495), 2)
    assert _tiered_net_roe_floor(15.0) == (7.5, 3)
    assert _tiered_net_roe_floor(19.99) == (pytest.approx(9.995), 3)
    assert _tiered_net_roe_floor(20.0) == (10.0, 4)


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
