"""回歸：第二個幣種（龍蝦）開倉金額必須與第一個幣種相同（錢包總額對分），
不可因第一個幣種已佔用保證金而只拿到剩餘可用餘額的一半。"""
import pytest

import core.config as config
from core.engine import TradingEngine


def test_second_symbol_gets_equal_half_of_wallet(monkeypatch):
    monkeypatch.setattr(config, "MAX_SLOTS", 2)
    wallet, leverage = 150.0, 5.0
    fee_buffer = 1.0 + leverage * config.TAKER_FEE_RATE

    first = TradingEngine._full_wallet_entry_margin(wallet, wallet, leverage, "1000PEPE/USDT")
    remaining = wallet - first * fee_buffer
    second = TradingEngine._full_wallet_entry_margin(wallet, remaining, leverage, "龙虾/USDT")

    assert first == pytest.approx(wallet / 2 * 0.98)
    assert second == pytest.approx(first)
    assert (first + second) * fee_buffer <= wallet

