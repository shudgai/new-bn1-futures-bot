from core.services.exits.authorized_position_exit import automated_position_exit_allowed


def test_exitgate_profit_lock_label_alone_cannot_authorize_close():
    # Arrange
    position = {"side": "LONG", "entry_price": 100.0, "qty": 1.0}

    # Act
    allowed = automated_position_exit_allowed(
        "TEST/USDT", position, {}, "ExitGate:EXIT_BY_RATCHET_PROFIT_LOCK", 102.0,
    )

    # Assert
    assert allowed is False


def test_verified_profit_lock_cannot_bypass_kc_reversal_only_policy(monkeypatch):
    import core.gates.holding_protection_gate as holding_gate

    # Arrange
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "breakeven_locked": True,
        "breakeven_price": 101.0,
    }
    monkeypatch.setattr(
        holding_gate,
        "_verified_profit_lock_details",
        lambda *_args, **_kwargs: {"lock_type": "VERIFIED_TEST_LOCK"},
    )

    # Neither a verified lock nor a pivot is independent close authority.
    no_pivot = automated_position_exit_allowed(
        "TEST/USDT", position, {}, "ExitGate:EXIT_BY_RATCHET_PROFIT_LOCK", 100.5,
    )
    monkeypatch.setattr(
        holding_gate,
        "has_confirmed_true_pivot",
        lambda *_args, **_kwargs: (True, "TRUE_PEAK", {"confirmed": True}),
    )
    with_pivot = automated_position_exit_allowed(
        "TEST/USDT", position, {}, "ExitGate:EXIT_BY_RATCHET_PROFIT_LOCK", 100.5,
        frame=object(),
    )

    # Assert
    assert no_pivot is False
    assert with_pivot is False
