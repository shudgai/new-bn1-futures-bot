"""獲利重開追高上限改以「距持倉側外軌幾個 ATR」衡量（2026-09-13 使用者要求）。

固定 2%（離上次平倉價）會把一路疊上去的延續與當根特例長K都擋掉；
改與 3 ATR 淨利空間同一把尺：只有「現價已離外軌太遠」才視為末端追價、
暫不重開（票據保留）。當根即時長K破軌是新訊號，不受此上限。
"""
import pytest

from core import config
from core.engine import TradingEngine
from test_channel_breakout_only import invalid_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

CLOSE_REASON = "Channel Swing PROFIT_PROTECTION chase-test"


def _engine(frame, side):
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.account.trades = [{
        "id": 1, "symbol": SYMBOL, "action": "CLOSE_" + side,
        "reason": CLOSE_REASON, "price": float(frame.iloc[-1]["close"]),
    }]
    return engine


def _ticket(side):
    return {
        "side": side, "phase": "closed", "mode": "outer_cycle",
        "requires_pullback": False, "exit_bar_id": 900000.0,
        "close_reason": CLOSE_REASON,
    }


def _frame_at_rail_gap(side, gap_atr, live_open_inside=False):
    frame, _price = invalid_frame(side, "no_cross")
    frame["timestamp"] = [(i + 1) * 60000 for i in range(len(frame))]
    atr = float(frame.iloc[-2]["atr"])
    sign = 1 if side == "LONG" else -1
    rail = float(frame.iloc[-1]["kc_upper" if side == "LONG" else "kc_lower"])
    price = rail + sign * gap_atr * atr
    live = frame.index[-1]
    # 非特例長K：當根實體保持小於過熱門檻，單獨驗證追高上限。
    frame.loc[live, "open"] = (rail - sign * 0.1) if live_open_inside else price - sign * 0.3 * atr
    frame.loc[live, "close"] = price
    frame.loc[live, "high"] = max(price, float(frame.loc[live, "open"])) + 0.1
    frame.loc[live, "low"] = min(price, float(frame.loc[live, "open"])) - 0.1
    return frame, price


def _ready(side, gap_atr, limit_atr, monkeypatch, live_open_inside=False):
    frame, price = _frame_at_rail_gap(side, gap_atr, live_open_inside=live_open_inside)
    monkeypatch.setattr(config, "CHANNEL_PROFIT_REENTRY_MAX_CHASE_ATR", limit_atr, raising=False)
    engine = _engine(frame, side)
    return engine, engine._profit_reentry_ready(SYMBOL, _ticket(side), frame, price)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("gap_atr, allowed", [(1.5, True), (2.0, True), (2.5, False)])
def test_chase_limit_measures_distance_beyond_outer_rail(side, gap_atr, allowed, monkeypatch):
    engine, ready = _ready(side, gap_atr, 2.0, monkeypatch)
    assert ready is allowed
    blocked = any("外軌" in text for text, _level in engine.account.logs)
    assert blocked is (not allowed)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_long_body_breakout_is_exempt_from_chase_limit(side, monkeypatch):
    # 當根開盤還在軌內側、報價剛破軌：這是新訊號，即使離軌 3 ATR 也不算末端追價。
    engine, ready_on = _ready(side, 3.0, 2.0, monkeypatch, live_open_inside=True)
    _engine_off, ready_off = _ready(side, 3.0, 0.0, monkeypatch, live_open_inside=True)
    assert not any("追高上限" in text for text, _level in engine.account.logs)
    assert ready_on is ready_off


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_chase_limit_off_when_zero(side, monkeypatch):
    engine_off, _ready_off = _ready(side, 6.0, 0.0, monkeypatch)
    engine_on, ready_on = _ready(side, 6.0, 2.0, monkeypatch)
    assert not any("追高上限" in text for text, _level in engine_off.account.logs)
    assert any("追高上限" in text for text, _level in engine_on.account.logs)
    assert ready_on is False

def _special_long_body_frame(side):
    """把倒數第二根（已收線）做成順向長實體並收在軌外，構成特例長K入口。"""
    frame, _price = invalid_frame(side, "no_cross")
    frame["timestamp"] = [(i + 1) * 60000 for i in range(len(frame))]
    sign = 1 if side == "LONG" else -1
    rail = float(frame.iloc[-1]["kc_upper" if side == "LONG" else "kc_lower"])
    idx = frame.index[-2]
    opened = rail - sign * 1.0
    closed = rail + sign * 2.0
    frame.loc[idx, ["open", "close"]] = [opened, closed]
    frame.loc[idx, "high"] = max(opened, closed) + 0.1
    frame.loc[idx, "low"] = min(opened, closed) - 0.1
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_long_body_special_entry_is_exempt_from_chase_limit(side, monkeypatch):
    frame = _special_long_body_frame(side)
    sign = 1 if side == "LONG" else -1
    rail = float(frame.iloc[-1]["kc_upper" if side == "LONG" else "kc_lower"])
    price = rail + sign * 4.0 * float(frame.iloc[-2]["atr"])
    frame.loc[frame.index[-1], ["open", "close"]] = [price - sign * 0.3, price]
    monkeypatch.setattr(config, "CHANNEL_LONG_BODY_ENTRY_ATR", 2.0, raising=False)
    assert TradingEngine._special_long_body_entry(frame, price, side) is True
    assert TradingEngine._terminal_market_exempt(frame, price, side) is True
    monkeypatch.setattr(config, "CHANNEL_PROFIT_REENTRY_MAX_CHASE_ATR", 2.0, raising=False)
    engine = _engine(frame, side)
    ready_on = engine._profit_reentry_ready(SYMBOL, _ticket(side), frame, price)
    monkeypatch.setattr(config, "CHANNEL_PROFIT_REENTRY_MAX_CHASE_ATR", 0.0, raising=False)
    ready_off = _engine(frame, side)._profit_reentry_ready(SYMBOL, _ticket(side), frame, price)
    assert not any("追高上限" in text for text, _level in engine.account.logs)
    assert ready_on is ready_off
