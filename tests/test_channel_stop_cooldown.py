"""停損後冷卻：避免停損後立刻反向再進場造成連續停損。

2026-09-11 後段實例：三筆在停損後 0.1~7.4 分鐘立刻反向再進場，全部再被停損，
單一時段吃掉當日 92% 的虧損。
"""
import time
from types import SimpleNamespace

import pytest

from core import config
from core.engine import TradingEngine


def _engine(last_stop_at):
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = SimpleNamespace(
        last_stop_at=last_stop_at, positions={}, pending_limit_orders={},
        log=lambda *args, **kwargs: None,
    )
    return engine


def test_cooldown_blocks_only_the_stopped_symbol(monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_STOP_LOSS_COOLDOWN_SEC", 900)
    engine = _engine({"龙虾/USDT": time.time() - 60})
    assert engine._channel_stop_cooldown_remaining("龙虾/USDT") == pytest.approx(840, abs=5)
    assert engine._channel_stop_cooldown_remaining("1000PEPE/USDT") == 0.0


def test_cooldown_expires(monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_STOP_LOSS_COOLDOWN_SEC", 900)
    assert _engine({"X/USDT": time.time() - 3600})._channel_stop_cooldown_remaining("X/USDT") == 0.0


def test_cooldown_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_STOP_LOSS_COOLDOWN_SEC", 0)
    assert _engine({"X/USDT": time.time()})._channel_stop_cooldown_remaining("X/USDT") == 0.0


def test_account_state_round_trips_stop_timestamp(tmp_path, monkeypatch):
    from core import paper_account as module

    monkeypatch.setattr(module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "paper_account.json"))
    account = module.PaperAccount()
    account.last_stop_at = {"X/USDT": 123.0}
    account.save_state()
    assert module.PaperAccount().last_stop_at == {"X/USDT": 123.0}
