from core.services.exits.authorized_position_exit import automated_position_exit_allowed


def test_trend_line_broken_exit_authorized_for_both_sides():
    for side in ('LONG', 'SHORT'):
        assert automated_position_exit_allowed(
            'X/USDT', {'side': side}, {}, 'ExitGate:EXIT_BY_TREND_LINE_BROKEN', 1.0,
        ) is True


def test_unknown_exit_reason_still_blocked():
    assert automated_position_exit_allowed(
        'X/USDT', {'side': 'LONG'}, {}, 'ExitGate:EXIT_BY_PULLBACK', 1.0,
    ) is False
