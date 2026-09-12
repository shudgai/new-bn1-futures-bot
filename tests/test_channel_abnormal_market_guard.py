"""異常拉砸守門：順向特例長K放行振幅限制，逆向與反向保護不變（2026-09-13 使用者要求）。"""
import pytest

from core import config
from core.engine import TradingEngine
from core.guards.abnormal_guard import opposite_entry_releases
from test_channel_aligned_entry import aligned_frame
from test_channel_swing_execution import _execution_engine, SYMBOL


def _engine():
    engine = object.__new__(TradingEngine)
    engine.account = type("_Account", (), {"_rapid_drop_cooldown": {}})()
    engine.account.log = lambda *args, **kwargs: None
    return engine


def _allowed(monkeypatch, side, candle, price=100.0, atr=1.0):
    monkeypatch.setattr("core.engine.ABNORMAL_MARKET_GUARD_ENABLED", True, raising=False)
    monkeypatch.setattr("core.engine.ABNORMAL_MARKET_MAX_CANDLE_RANGE_ATR", 4.0, raising=False)
    monkeypatch.setattr("core.engine.ABNORMAL_MARKET_MAX_CANDLE_RANGE_PCT", 0.025, raising=False)
    monkeypatch.setattr("core.engine.ABNORMAL_MARKET_ADVERSE_MOVE_PCT", 0.012, raising=False)
    monkeypatch.setattr(config, "CHANNEL_LIVE_BREAKOUT_BODY_ATR", 1.0, raising=False)
    opened, high, low, closed = candle
    return _engine()._abnormal_market_entry_allowed(
        "TEST/USDT", side, price, atr, opened, high, low, closed)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_aligned_special_long_body_passes_huge_range(side, monkeypatch):
    sign = 1 if side == "LONG" else -1
    opened, closed = 100.0, 100.0 + sign * 4.0
    high, low = max(opened, closed) + 0.5, min(opened, closed) - 0.5
    assert _allowed(monkeypatch, side, (opened, high, low, closed)) is True


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_opposite_impulse_still_blocked(side, monkeypatch):
    sign = 1 if side == "LONG" else -1
    opened, closed = 100.0, 100.0 - sign * 4.0
    high, low = max(opened, closed) + 0.5, min(opened, closed) - 0.5
    assert _allowed(monkeypatch, side, (opened, high, low, closed)) is False


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_huge_range_with_small_body_still_blocked(side, monkeypatch):
    sign = 1 if side == "LONG" else -1
    opened = 100.0
    closed = 100.0 + sign * 0.5
    high, low = 104.0, 96.0
    assert _allowed(monkeypatch, side, (opened, high, low, closed)) is False


def _release_engine(side):
    frame = aligned_frame(side, 'breakout')
    frame['timestamp'] = [(i + 1) * 60_000 for i in range(len(frame))]
    # 需要「已收線 CK 中軌動能仍在加速」才符合同向新倉條件。
    sign = 1 if side == 'LONG' else -1
    mid = float(frame.iloc[-3]['kc_middle'])
    frame.loc[frame.index[-2], 'kc_middle'] = mid + sign * 0.7
    price = float(frame.iloc[-1]['close'])
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.tickers[SYMBOL] = price
    old = 'SHORT' if side == 'LONG' else 'LONG'
    reason = 'Channel Swing PROFIT_PROTECTION 1789182513.6909657:1789182539501313197'
    engine.account.channel_profit_reentries = {SYMBOL: dict(
        token='profit', phase='closed', side=old, mode='outer_cycle',
        requires_pullback=False, close_reason=reason,
        exit_bar_id=960000, close_requested_at_ms=960001)}
    engine.account.trades = [dict(symbol=SYMBOL, action='CLOSE_' + old, reason=reason, id=960002)]
    return engine, frame, price


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_profit_protection_ticket_releases_on_confirmed_reverse(side):
    """2026-09-13：賺錢落袋（階梯鎖利）的票據原本卡到過期，現在方向確認反轉就解除。"""
    engine, frame, price = _release_engine(side)
    assert opposite_entry_releases(engine.account, SYMBOL, frame, price) is True
    flat = frame.copy()
    flat['kc_middle'] = 100.
    assert opposite_entry_releases(engine.account, SYMBOL, flat, price) is False
    engine.account.channel_profit_reentries[SYMBOL]['requires_pullback'] = True
    assert opposite_entry_releases(engine.account, SYMBOL, frame, price) is False
