import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.engine import TradingEngine
from core.services.entry_contract import evaluate_continuation_entry
from test_breakout_only_entry import breakout_frame


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("unknown_channel", "REJECT_FLAT_CHANNEL"),
        ("flat_upper", "REJECT_FLAT_CHANNEL"),
        ("unclosed_breakout", "BLOCKED_CONTINUATION_NOT_CLOSED_CONFIRMATION"),
    ],
)
def test_long_continuation_requires_rising_known_kc_and_closed_confirmation(
    case, expected_reason,
):
    frame = breakout_frame("LONG")
    live = frame.index[-1]
    frame.loc[live, ["open", "close", "high", "low",
                     "kc_upper", "kc_middle", "kc_lower"]] = [
        101.4, 101.6, 101.6, 101.4, 101.8, 100.3, 98.8,
    ]
    quote = 101.9
    latest_closed = frame.index[-2]
    previous_closed = frame.index[-3]

    if case == "unknown_channel":
        middle = float(frame.loc[latest_closed, "kc_middle"])
        frame.loc[latest_closed, ["close", "ma5", "ma15"]] = [
            middle, middle, middle,
        ]
        frame.loc[previous_closed, ["kc_middle", "ma5", "ma15"]] = [
            middle, middle, middle,
        ]
    elif case == "flat_upper":
        frame.loc[latest_closed, "kc_upper"] = frame.loc[previous_closed, "kc_upper"]
    else:
        frame.loc[latest_closed, "close"] = (
            frame.loc[latest_closed, "kc_upper"] - 0.1
        )

    diagnostics = {}
    decision = evaluate_continuation_entry(
        frame, quote, symbol="SYM", account=SimpleNamespace(positions={}, trades=[]),
        diagnostics=diagnostics,
    )

    assert quote > float(frame.iloc[-1]["kc_upper"])
    assert decision is None
    assert diagnostics["reason"] == expected_reason


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_valid_breakout_is_remembered_when_order_is_blocked(side):
    frame = breakout_frame(side)
    quote = float(frame.iloc[-1]['close'])
    from core.services.entry_contract import evaluate_entry_contract
    entry = evaluate_entry_contract(frame, quote, symbol='SYM')
    assert entry is not None
    account = SimpleNamespace(positions={}, trades=[], last_closed_at={},
                              breakout_qualification={}, log=Mock())
    def record(symbol, value):
        account.breakout_qualification[symbol] = value
    account.record_qualification = Mock(side_effect=record)
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    result = asyncio.run(engine._execute_confirmed_channel_break(
        'SYM', frame, quote, side, daily_halt=True,
        v8_reason=entry['type']))
    assert result is False
    qual = account.breakout_qualification['SYM']
    assert qual['side'] == side
    assert qual['breakout_bar_id'] == entry['confirmation_bar_id']
    # A qualification becomes eligible on a later candle, not the signal candle.
    assert evaluate_continuation_entry(frame, quote, symbol='SYM', account=account) is None
    frame.loc[frame.index[-1], 'is_closed'] = True
    frame.loc[frame.index[-1] + 1] = dict(
        timestamp=float(frame.iloc[-1]['timestamp']) + 60_000.,
        open=101.7, close=102., high=102.1, low=101.6,
        kc_upper=101.5, kc_middle=100.4, kc_lower=99.4,
        ma5=100.9, ma15=100.1, atr=1., is_closed=False,
    )
    if side == 'SHORT':
        row = frame.iloc[-1].copy()
        frame.loc[frame.index[-1], ['open', 'close', 'high', 'low',
                                    'kc_upper', 'kc_middle', 'kc_lower',
                                    'ma5', 'ma15']] = [
            200. - row['open'], 200. - row['close'], 200. - row['low'],
            200. - row['high'], 200. - row['kc_lower'],
            200. - row['kc_middle'], 200. - row['kc_upper'],
            200. - row['ma5'], 200. - row['ma15'],
        ]
    quote = 102.3 if side == 'LONG' else 97.7
    below_threshold_quote = 102.1 if side == 'LONG' else 97.9
    assert evaluate_continuation_entry(
        frame, below_threshold_quote, symbol='SYM', account=account
    ) is None
    decision = evaluate_continuation_entry(frame, quote, symbol='SYM', account=account)
    assert decision is not None
    assert decision['qualification_signal_id'] == qual['pending_signal_id']
    edge = frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower']
    assert evaluate_continuation_entry(frame, edge, symbol='SYM', account=account) is None
    account.breakout_qualification['SYM']['side'] = 'SHORT' if side == 'LONG' else 'LONG'
    assert evaluate_continuation_entry(frame, quote, symbol='SYM', account=account) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_legacy_live_body_breakout_never_creates_qualification(side):
    frame = breakout_frame(side)
    quote = float(frame.iloc[-1].open) + (.9 if side == 'LONG' else -.9)
    account = SimpleNamespace(positions={}, trades=[], last_closed_at={},
                              breakout_qualification={}, log=Mock(), record_qualification=Mock())
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = account
    asyncio.run(engine._execute_confirmed_channel_break(
        'SYM', frame, quote, side, daily_halt=True,
        v8_reason='KC_LIVE_BODY_BREAKOUT_'+side))
    account.record_qualification.assert_not_called()
