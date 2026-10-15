"""Adapter fidelity checks: green here does NOT mean staged policy passed."""

import math
import socket
import sys
from unittest.mock import Mock

import pytest

from my_package import red_eye_adapter as bridge
from red_eye_support import MockExchange, PositionState, RISK_PARAMS, ReferenceStagedStrategy


@pytest.fixture(autouse=True)
def prohibit_external_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Red-eye tests attempted external networking")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def price_for_net(net):
    # Fixture construction only: invert fee/slippage valuation, not risk logic.
    return (100 * 1.0005 + net / 10) / (0.9995 * 0.9995)












