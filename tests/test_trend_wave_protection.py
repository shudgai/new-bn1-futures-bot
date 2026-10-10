import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.entry_contract import check_entry_gates
from core.services.exits.realtime_profit_exit import (
    _same_color_ma5_pressure,
    enforce_realtime_profit_exit,
)


def _exit_frame(side='SHORT'):
    bar_ms = int(time.time() // 60) * 60_000
    rows = []
    for i in range(5):
        close = 100. - i if side == 'SHORT' else 100. + i
        ma5 = 101. - i if side == 'SHORT' else 99. + i
        rows.append(dict(
            timestamp=bar_ms - (5 - i) * 60_000, is_closed=True,
            open=close + 0.2 if side == 'SHORT' else close - 0.2,
            high=close + 0.3, low=close - 0.3, close=close,
            atr=1., ma3=ma5, ma5=ma5, ma15=ma5,
            kc_upper=ma5 + 2., kc_middle=ma5, kc_lower=ma5 - 2.,
        ))
    live_open = 95. if side == 'SHORT' else 105.
    rows.append(dict(
        timestamp=bar_ms, is_closed=False, open=live_open,
        high=live_open + .1, low=live_open - .1, close=live_open,
        atr=1., ma3=live_open, ma5=live_open, ma15=live_open,
        kc_upper=live_open + 2., kc_middle=live_open,
        kc_lower=live_open - 2.,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


def test_same_color_bodies_and_monotone_ma5_lock_short_trend():
    snapshot = {'history_5': [
        {'o': 102., 'c': 101., 'ma5': 103.},
        {'o': 101., 'c': 100., 'ma5': 102.},
        {'o': 100., 'c': 99., 'ma5': 101.},
    ]}
    assert _same_color_ma5_pressure({'side': 'SHORT'}, snapshot)
    snapshot['history_5'][-1]['c'] = snapshot['history_5'][-1]['o']
    assert not _same_color_ma5_pressure({'side': 'SHORT'}, snapshot)


def test_channel_short_doji_or_v_reversal_cannot_close_mid_trend(monkeypatch):
    async def run():
        now = time.time()
        position = dict(
            side='SHORT', open_timestamp=now - 120, entry_price=100., qty=1.,
            margin=100., leverage=1., entry_mode='CHANNEL_SWING',
        )
        account = SimpleNamespace(
            positions={'X': position}, position_meta={},
            close_position=AsyncMock(return_value=True), save_state=Mock(), log=Mock(),
        )
        engine = SimpleNamespace(
            account=account, is_running=True,
            _channel_exit_frames={'X': _exit_frame('SHORT')},
        )
        monkeypatch.setattr(
            'core.services.exits.realtime_profit_exit.enforce_hard_stop',
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            'core.services.exits.realtime_profit_exit._evaluate_realtime_core_exit_gates',
            lambda *_a, **_k: (None, False),
        )
        monkeypatch.setattr(
            'core.services.exits.peak_trailing_exit.v_reversal_short_exit_trigger',
            lambda *_a, **_k: 'V_REVERSAL_SHORT_KC_LOWER_RECLAIM',
        )

        assert not await enforce_realtime_profit_exit(
            engine, 'X', 95., quote_ms=now * 1000,
        )
        account.close_position.assert_not_awaited()
        assert any('STRUCTURE_OR_NET_ROE_ONLY' in call.args[0]
                   for call in account.log.call_args_list)

    asyncio.run(run())


def test_channel_short_can_close_on_completed_ma5_structure(monkeypatch):
    async def run():
        now = time.time()
        position = dict(
            side='SHORT', open_timestamp=now - 120, entry_price=100., qty=1.,
            margin=100., leverage=1., entry_mode='CHANNEL_SWING',
        )
        account = SimpleNamespace(
            positions={'X': position}, position_meta={},
            close_position=AsyncMock(return_value=True), save_state=Mock(), log=Mock(),
        )
        engine = SimpleNamespace(
            account=account, is_running=True,
            _channel_exit_frames={'X': _exit_frame('SHORT')},
        )
        monkeypatch.setattr(
            'core.services.exits.realtime_profit_exit.enforce_hard_stop',
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            'core.services.exits.realtime_profit_exit._evaluate_realtime_core_exit_gates',
            lambda *_a, **_k: ('ONE_MINUTE_CLOSED_MA5_TREND_GUARD', False),
        )

        assert await enforce_realtime_profit_exit(
            engine, 'X', 95., quote_ms=now * 1000,
        )
        account.close_position.assert_awaited_once()
        assert 'ONE_MINUTE_CLOSED_MA5_TREND_GUARD' in account.close_position.await_args.args[2]

    asyncio.run(run())


def test_account_poll_cannot_close_channel_position_on_doji_or_deceleration(monkeypatch):
    from core.services.exits.entry_atr_protection import enforce_atr_protection

    async def run():
        position = dict(
            side='SHORT', open_timestamp=time.time() - 120,
            entry_price=100., qty=1., margin=100., leverage=1.,
            entry_mode='CHANNEL_SWING',
        )
        account = SimpleNamespace(
            positions={'X': position}, position_meta={},
            close_position=AsyncMock(return_value=True),
            save_state=Mock(), log=Mock(),
        )
        monkeypatch.setattr(
            'core.services.exits.peak_trailing_exit.migrate_peak_state',
            lambda *_a, **_k: None,
        )
        monkeypatch.setattr(
            'core.services.exits.peak_trailing_exit.evaluate_peak_trailing',
            lambda *_a, **_k: {
                'type': 'EXIT_ADVERSE_ABNORMAL_BODY',
                'trigger': 'EXIT_CONSECUTIVE_DOJI_STALL',
            },
        )

        assert not await enforce_atr_protection(account, 'X', 99.9)
        account.close_position.assert_not_awaited()
        assert any('HOLD_NON_LOCK_ACCOUNT_POLL' in call.args[0]
                   for call in account.log.call_args_list)

    asyncio.run(run())


def _extended_breakout_bars(*, aligned):
    rows = []
    for i in range(12):
        middle = 100. - i if aligned else 100. + i
        ma5 = 99. - i if aligned else 99. + i
        lower = middle - 1.
        opening = lower - .2
        close = opening - .8
        rows.append(dict(
            timestamp=i * 60_000, open=opening, high=opening + .05,
            low=close - .05, close=close, atr=1., ma5=ma5,
            ma15=ma5 - 1., kc_lower=lower, kc_middle=middle,
            kc_upper=middle + 2., volume=100.,
        ))
    return pd.DataFrame(rows)


@pytest.mark.parametrize('aligned', [True, False])
def test_extended_short_breakout_only_relaxes_under_falling_kc_and_ma5(monkeypatch, aligned):
    monkeypatch.setattr(
        'core.services.entry_contract.continuation_entry_problem',
        lambda *_a, **_k: None,
    )
    passed, reason = check_entry_gates(
        None, 'CAP/USDT', _extended_breakout_bars(aligned=aligned),
        'SHORT', 'TRIGGER_A_KC_BREAKOUT',
    )
    if aligned:
        assert passed
        assert reason == 'GATE_PASSED_FAST_LANE_EXTENDED_TREND'
    else:
        assert not passed
        assert reason == 'BLOCKED_BY_EXTENDED_BREAKOUT_GATE'


def _post_close_impulse_frame(bars_after_close=3):
    from core.services.three_bar_rail_gate import three_bar_rail_gate_problem

    live_stamp = int(time.time() // 60) * 60_000
    rows = []
    for offset in (7, 6, 5, 4, 3):
        close = 104. - offset
        rows.append(dict(
            timestamp=live_stamp - offset * 60_000, is_closed=True,
            open=close + .2, high=close + .3, low=close - .1, close=close,
            atr=1., ma5=close + 1., ma15=close + 1.5,
            kc_lower=close - 1.5 if offset == 3 else close - 1.,
            kc_middle=close + .5, kc_upper=close + 2., volume=100.,
        ))
    rows.append(dict(
        timestamp=live_stamp - 2 * 60_000, is_closed=True,
        open=96., high=96.1, low=95.5, close=95.6, atr=1., ma5=96.,
        ma15=97., kc_lower=94.5, kc_middle=97., kc_upper=99., volume=100.,
    ))
    # Third fully completed minute after close: strong bearish body and prior-low break.
    rows.append(dict(
        timestamp=live_stamp - 60_000, is_closed=True,
        open=96., high=96.1, low=93.5, close=93.8, atr=1., ma5=95.,
        ma15=96., kc_lower=94.2, kc_middle=96., kc_upper=98., volume=100.,
    ))
    rows.append(dict(
        timestamp=live_stamp, is_closed=False,
        open=94., high=94.1, low=93.4, close=93.8, atr=1., ma5=94.,
        ma15=95., kc_lower=94.1, kc_middle=95., kc_upper=97., volume=100.,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    close_id = live_stamp - (1 + bars_after_close) * 60_000 + 30_000
    account = SimpleNamespace(
        positions={},
        trades=[dict(
            id=close_id, symbol='CAP/USDT', action='CLOSE_SHORT',
            side='SHORT', status='CLOSED', price=100.,
        )],
    )
    quote = 93.6
    # The existing Three-Bar gate would reject this because Bar 1 did not break KC.
    assert three_bar_rail_gate_problem(frame, quote, 'SHORT') == (
        'BLOCKED_THREE_BAR_SHORT_NOT_BROKEN_LOWER_RAIL'
    )
    return frame, account, quote


@pytest.mark.parametrize('bars_after_close', [1, 3])
def test_strong_post_close_impulse_cannot_bypass_three_bar_rail_gate(
    monkeypatch, bars_after_close,
):
    from core.services.entry_contract import evaluate_entry_contract

    monkeypatch.setattr(
        'core.services.entry_contract.entry_direction_problem',
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        'core.services.entry_contract.anti_bottom_short_problem',
        lambda *_a, **_k: None,
    )
    frame, account, quote = _post_close_impulse_frame(bars_after_close)

    diagnostics = {}
    decision = evaluate_entry_contract(
        frame, quote, code='TRIGGER_C_CONTINUATION',
        account=account, symbol='CAP/USDT', diagnostics=diagnostics,
    )
    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_INSIDE_KC_BANDS'


def test_ordinary_continuation_remains_subject_to_three_bar_gate(monkeypatch):
    from core.services.entry_contract import evaluate_entry_contract

    frame, account, quote = _post_close_impulse_frame()
    account.trades = []
    monkeypatch.setattr(
        'core.services.entry_contract.evaluate_post_close_continuation',
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        'core.services.entry_contract.evaluate_continuation_entry',
        lambda *_a, **_k: dict(
            action='ENTER', side='SHORT', type='TRIGGER_C_CONTINUATION',
            reason='ORDINARY_CONTINUATION', entry_phase='KC_CONTINUATION_ENTRY',
            confirmation_bar_id=float(frame.iloc[-1]['timestamp']),
            breakout_bar_id=float(frame.iloc[-2]['timestamp']),
            pair_confirmation_bar_id=float(frame.iloc[-3]['timestamp']),
            pending_signal_id='ordinary-short',
        ),
    )
    monkeypatch.setattr(
        'core.services.entry_contract.short_hard_preentry_problem',
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        'core.services.entry_contract.three_bar_rail_gate_problem',
        lambda *_a, **_k: 'BLOCKED_THREE_BAR_SHORT_NOT_BROKEN_LOWER_RAIL',
    )
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, quote, code='TRIGGER_C_CONTINUATION',
        account=account, symbol='CAP/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_THREE_BAR_SHORT_NOT_BROKEN_LOWER_RAIL'


def test_strong_post_close_impulse_four_bars_later_is_rejected():
    from core.services.entry_contract import evaluate_post_close_continuation

    frame, account, quote = _post_close_impulse_frame()
    # Shift the close so the otherwise identical completed impulse is four bars later.
    latest_closed_ms = float(frame.iloc[-2]['timestamp'])
    close_floor = latest_closed_ms - 4 * 60_000
    account.trades[0]['id'] = close_floor + 10_000
    assert evaluate_post_close_continuation(
        frame, quote, 'CAP/USDT', account,
    ) is None
