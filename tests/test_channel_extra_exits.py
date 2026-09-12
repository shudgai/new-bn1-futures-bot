"""量能衰退平倉與 MA3 轉進軌內後的單根反向異常K即時平倉。"""
from types import SimpleNamespace
import pandas as pd
import pytest

from core.guards import abnormal_guard
from core.guards.abnormal_guard import channel_adverse_exit_reason
from core.strategy import has_real_volume_decay
from core.services.swing_service import volume_decay_exit_ready

ATR = 1.0
REASON = "EMERGENCY_EXIT_MA3_ENTERED_RAIL_ADVERSE_BAR"


def _frame(ma3_before, ma3_last, lower, closed_bodies, live_body):
    """兩根已收線K（iloc[-3]、iloc[-2]）加一根即時K；軌道固定。"""
    rows = [
        {"open": 100.0, "close": 100.0 + closed_bodies[0], "ma3": ma3_before,
         "kc_lower": lower, "kc_upper": lower + 10.0},
        {"open": 100.0, "close": 100.0 + closed_bodies[1], "ma3": ma3_last,
         "kc_lower": lower, "kc_upper": lower + 10.0},
        {"open": 100.0, "close": 100.0 + live_body, "ma3": ma3_last,
         "kc_lower": lower, "kc_upper": lower + 10.0},
    ]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("side,sign,ma3_before,ma3_last", [
    ("SHORT", 1.0, 90.0, 96.0),    # MA3 由下軌外轉進軌內
    ("LONG", -1.0, 110.0, 104.0),  # MA3 由上軌外轉進軌內
])
def test_single_adverse_bar_exits_after_ma3_reenters_the_rail(side, sign, ma3_before, ma3_last):
    frame = _frame(ma3_before, ma3_last, 95.0, [0.2 * sign, 1.2 * sign], 0.1 * sign)
    assert channel_adverse_exit_reason(frame, side, 100.0 + 0.1 * sign, ATR) == REASON


@pytest.mark.parametrize("side,sign,ma3_before,ma3_last", [
    ("SHORT", 1.0, 90.0, 92.0),   # 仍在下軌外
    ("LONG", -1.0, 110.0, 108.0),  # 仍在上軌外
])
def test_no_single_exit_while_ma3_stays_outside_the_rail(side, sign, ma3_before, ma3_last):
    frame = _frame(ma3_before, ma3_last, 95.0, [0.2 * sign, 1.2 * sign], 0.1 * sign)
    assert channel_adverse_exit_reason(frame, side, 100.0 + 0.1 * sign, ATR) is None


@pytest.mark.parametrize("side,sign", [("SHORT", 1.0), ("LONG", -1.0)])
def test_no_single_exit_when_ma3_never_left_the_rail(side, sign):
    frame = _frame(97.0, 97.0, 95.0, [0.2 * sign, 1.2 * sign], 0.1 * sign)
    assert channel_adverse_exit_reason(frame, side, 100.0 + 0.1 * sign, ATR) is None


def test_single_adverse_exit_can_be_disabled(monkeypatch):
    monkeypatch.setattr(abnormal_guard, "CHANNEL_SINGLE_ADVERSE_EXIT_ENABLED", False)
    frame = _frame(90.0, 96.0, 95.0, [0.2, 1.2], 0.1)
    assert channel_adverse_exit_reason(frame, "SHORT", 100.1, ATR) is None


def test_two_closed_bodies_still_exit_first():
    frame = _frame(90.0, 96.0, 95.0, [1.2, 1.4], 0.1)
    assert channel_adverse_exit_reason(frame, "SHORT", 100.1, ATR) == "EMERGENCY_EXIT_2_CANDLE_ADVERSE"


def _volume_frame(recent_volume, extreme_volume=None, rows=21):
    """rows-1 根已收線 + 1 根未收線；倒數第二根（最後已收線）創新高／新低。"""
    half = (rows - 1) // 2
    volumes = [100.0] * half + [recent_volume] * (rows - half)
    lows = [90.0] * rows
    highs = [110.0] * rows
    lows[-2] = 89.0    # 空單方向：最後已收線的價格創新低
    highs[-2] = 111.0  # 多單方向：最後已收線的價格創新高
    frame = pd.DataFrame({"low": lows, "high": highs, "volume": volumes})
    if extreme_volume is not None:
        frame.loc[frame.index[-2], "volume"] = extreme_volume
    return frame


def _decay_frame(recent_volume, ma3_before, ma3_last):
    frame = _volume_frame(recent_volume)
    frame["ma3"] = [100.0] * len(frame)
    frame.loc[frame.index[-3], "ma3"] = ma3_before
    frame.loc[frame.index[-2], "ma3"] = ma3_last
    return frame


def test_real_volume_decay_accepts_genuine_shrinking_volume():
    assert has_real_volume_decay(_volume_frame(20.0), 1) is True
    assert has_real_volume_decay(_volume_frame(20.0), -1) is True


def test_real_volume_decay_rejects_flat_or_rising_volume():
    assert has_real_volume_decay(_volume_frame(100.0), 1) is False
    assert has_real_volume_decay(_volume_frame(120.0), -1) is False


def test_real_volume_decay_rejects_extreme_made_on_high_volume():
    """創新極值那一根是爆量（高潮）而不是量縮 -> 不是真衰退。"""
    assert has_real_volume_decay(_volume_frame(20.0, extreme_volume=200.0), 1) is False


def test_real_volume_decay_ignores_single_early_volume_spike():
    """前段單一根爆量不得讓判定失真（舊版用平均值會被騙）。"""
    from core.strategy import has_volume_divergence

    frame = _volume_frame(100.0)          # 實際上量能沒有衰退
    frame.loc[frame.index[5], "volume"] = 5000.0
    assert has_volume_divergence(frame, 1) is True   # 舊版：被單根爆量騙了
    assert has_real_volume_decay(frame, 1) is False  # 新版：中位數不受影響


def test_real_volume_decay_ignores_the_unclosed_bar():
    """未收線K的量天生偏小，不能當成衰退證據。"""
    frame = _volume_frame(100.0)
    frame.loc[frame.index[-1], "volume"] = 1.0
    assert has_real_volume_decay(frame, 1) is False


def test_volume_decay_exit_matches_the_strict_detector(monkeypatch):
    frame = _decay_frame(20.0, 100.0, 100.6)
    assert volume_decay_exit_ready(frame, "SHORT", net_profitable=False) is True
    assert volume_decay_exit_ready(_decay_frame(100.0, 100.0, 100.6), "SHORT", net_profitable=False) is False


def test_profit_reentry_cooldown_blocks_immediate_reopen(monkeypatch):
    import time
    from core.engine import TradingEngine

    monkeypatch.setattr("core.engine.CHANNEL_PROFIT_REENTRY_COOLDOWN_SEC", 300)
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = SimpleNamespace(positions={}, log=lambda *args, **kwargs: None)
    fresh = {"phase": "closed", "side": "LONG", "close_requested_at_ms": time.time() * 1000}
    assert engine._profit_reentry_ready("X/USDT", fresh, None, 100.0) is False


def test_profit_reentry_cooldown_can_be_disabled(monkeypatch):
    import time
    from core.engine import TradingEngine

    monkeypatch.setattr("core.engine.CHANNEL_PROFIT_REENTRY_COOLDOWN_SEC", 0)
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = SimpleNamespace(positions={}, log=lambda *args, **kwargs: None,
                                     save_state=lambda: None)
    fresh = {"phase": "closed", "side": "LONG", "close_requested_at_ms": time.time() * 1000,
             "mode": "ck_reverse"}
    assert engine._profit_reentry_ready("X/USDT", fresh, None, 100.0) is False  # ck_reverse 一律不重開


def _trend_frame(price, strong=True):
    import pandas as pd

    width = 1.0
    middle = 100.0
    previous, latest = (99.8, 100.0) if strong else (99.99, 100.0)
    rows = [{"open": middle, "close": middle, "kc_middle": middle,
             "kc_upper": middle + width / 2, "kc_lower": middle - width / 2} for _ in range(4)]
    rows[-3]["kc_middle"] = previous
    rows[-2]["kc_middle"] = latest
    rows[-1]["close"] = price
    return pd.DataFrame(rows)


def test_strong_trend_exempts_the_stop_cooldown(monkeypatch):
    import time
    from core.engine import TradingEngine

    monkeypatch.setattr("core.engine.CHANNEL_STRONG_TREND_RATIO", 0.20)
    monkeypatch.setattr("core.engine.CHANNEL_STRONG_TREND_EXEMPTS_COOLDOWN", True)
    engine = TradingEngine.__new__(TradingEngine)
    engine.tickers = {"X/USDT": 101.0}
    engine._channel_exit_frames = {"X/USDT": _trend_frame(101.0, strong=True)}
    assert engine._channel_strong_trend("X/USDT", "LONG") is True
    engine._channel_exit_frames = {"X/USDT": _trend_frame(101.0, strong=False)}
    assert engine._channel_strong_trend("X/USDT", "LONG") is False


def test_strong_trend_exemption_can_be_disabled(monkeypatch):
    from core.engine import TradingEngine

    monkeypatch.setattr("core.engine.CHANNEL_STRONG_TREND_EXEMPTS_COOLDOWN", False)
    engine = TradingEngine.__new__(TradingEngine)
    engine.tickers = {"X/USDT": 101.0}
    engine._channel_exit_frames = {"X/USDT": _trend_frame(101.0, strong=True)}
    assert engine._channel_strong_trend("X/USDT", "LONG") is False


def _middle_cross_frame(side, crossed):
    import pandas as pd

    if side == "SHORT":
        ma3 = (100.4, 100.6) if crossed else (101.4, 101.6)
        middle = (100.5, 100.5)
        price = 100.7
    else:
        ma3 = (100.6, 100.4) if crossed else (99.6, 99.4)
        middle = (100.5, 100.5)
        price = 100.3
    return pd.DataFrame({
        "ma3": [*ma3, ma3[-1]],
        "kc_middle": [*middle, middle[-1]],
        "kc_upper": [101.5] * 3,
        "kc_lower": [99.5] * 3,
        "close": [price] * 3,
    })


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma3_middle_cross_closes_the_position(side):
    from core.services.swing_service import ma3_middle_cross_against

    assert ma3_middle_cross_against(_middle_cross_frame(side, crossed=True), side) is True
    assert ma3_middle_cross_against(_middle_cross_frame(side, crossed=False), side) is False


def test_ma3_middle_cross_needs_price_on_the_reversal_side():
    from core.services.swing_service import ma3_middle_cross_against

    frame = _middle_cross_frame("SHORT", crossed=True)
    frame.loc[frame.index[-2], "close"] = 100.2  # 價格仍在中軌下方
    assert ma3_middle_cross_against(frame, "SHORT") is False


def _flat_middle_frame(rows=24):
    import pandas as pd

    data = {
        "open": [100.0] * rows, "close": [100.0] * rows, "high": [100.2] * rows,
        "low": [99.8] * rows, "kc_upper": [102.0] * rows, "kc_lower": [98.0] * rows,
        "kc_middle": [100.0] * rows, "ema_20": [100.0] * rows,
        "ma3": [100.0] * rows, "ma15": [100.0] * rows, "atr": [1.0] * rows,
    }
    return pd.DataFrame(data)


def test_direction_efficiency_measures_one_way_progress():
    from core.services.strategies.outer_strategy import direction_efficiency

    frame = _flat_middle_frame()
    frame.loc[frame.index[-21:-1], "close"] = [100.0 + 0.1 * i for i in range(20)]
    assert direction_efficiency(frame) > 0.9
    noisy = _flat_middle_frame()
    noisy.loc[noisy.index[-21:-1], "close"] = [100.0 + (0.1 if i % 2 else -0.1) for i in range(20)]
    assert direction_efficiency(noisy) < 0.2


def test_long_body_entry_ignores_ck_middle_direction(monkeypatch):
    from core.services.strategies import outer_strategy

    frame = _flat_middle_frame()
    frame.loc[frame.index[-2], ["open", "close"]] = [101.0, 103.0]   # 長綠K且收在上軌外
    frame.loc[frame.index[-2], "kc_upper"] = 102.0
    assert outer_strategy.long_body_side(frame, 1.5) == "LONG"
    assert outer_strategy.long_body_side(frame, 3.0) is None         # 實體不足 3 ATR


def _breakout_frame(body_atr, atr=1.0):
    import pandas as pd

    rows = []
    for _ in range(2):
        rows.append({"open": 100.0, "close": 100.0, "kc_upper": 101.0, "kc_lower": 99.0,
                     "ma3": 100.0, "kc_middle": 100.0, "atr": atr})
    rows.append({"open": 100.0, "close": 100.0 + body_atr, "kc_upper": 101.0,
                 "kc_lower": 99.0, "ma3": 100.0, "kc_middle": 100.0, "atr": atr})
    return pd.DataFrame(rows)


def test_live_breakout_threshold_comes_from_config(monkeypatch):
    from core.services.strategies import outer_strategy

    frame = _breakout_frame(body_atr=2.0)
    price = 102.0
    monkeypatch.setattr(outer_strategy, "LIVE_BREAKOUT_BODY_ATR", 0.5)
    assert outer_strategy.live_body_breakout_side(frame, price) == "LONG"
    monkeypatch.setattr(outer_strategy, "LIVE_BREAKOUT_BODY_ATR", 1.0)
    assert outer_strategy.live_body_breakout_side(frame, price) == "LONG"
    monkeypatch.setattr(outer_strategy, "LIVE_BREAKOUT_BODY_ATR", 3.0)
    assert outer_strategy.live_body_breakout_side(frame, price) is None


def test_long_body_entry_uses_a_short_target(monkeypatch):
    from core import config
    from core.services.exits.profit_protection_service import protection

    monkeypatch.setattr(config, "CHANNEL_ATR_EXIT_ENABLED", True)
    monkeypatch.setattr(config, "CHANNEL_ATR_STOP_MULT", 1.5)
    monkeypatch.setattr(config, "CHANNEL_ATR_TARGET_MULT", 3.0)
    monkeypatch.setattr(config, "CHANNEL_ATR_LONG_BODY_TARGET_MULT", 1.0)
    trend = {"side": "LONG", "entry_price": 100.0, "qty": 3.75, "open_timestamp": 1.0,
             "atr": 1.0, "reason": "Channel Swing KC_TREND_LONG"}
    long_body = dict(trend, reason="Channel Swing KC_LIVE_BODY_BREAKOUT_LONG")
    assert protection(trend, 100.5, 0.0005, 0.0001)["target_price"] == pytest.approx(103.0)
    assert protection(long_body, 100.5, 0.0005, 0.0001)["target_price"] == pytest.approx(101.0)


def test_long_body_entry_is_not_blocked_by_flat_or_overheat(monkeypatch):
    """長K／破軌入口本身就是靠大實體成立，不能被走平或過熱過濾擋掉。"""
    import pandas as pd
    from core.services.strategies import outer_strategy

    monkeypatch.setattr(outer_strategy, "CHANNEL_LONG_BODY_ENTRY_ATR", 2.0)
    monkeypatch.setattr(outer_strategy, "CHANNEL_LIVE_BODY_BREAKOUT_ENABLED", False)
    monkeypatch.setattr(outer_strategy, "CHANNEL_FLAT_MIDDLE_RATIO", 0.05)
    monkeypatch.setattr(outer_strategy, "CHANNEL_MIN_DIRECTION_EFFICIENCY", 0.30)
    monkeypatch.setattr(outer_strategy, "CHANNEL_TAIL_MAX_TREND_BARS", 12)

    rows = []
    for _ in range(20):
        rows.append({"open": 100.0, "close": 100.0, "high": 100.1, "low": 99.9,
                     "kc_upper": 101.0, "kc_lower": 99.0, "kc_middle": 100.0,
                     "ema_20": 100.0, "ma3": 100.0, "ma15": 100.0, "atr": 1.0})
    # 最後一根已收線：長綠實體 2.5 ATR 且收在上軌外；中軌完全沒動（走平）
    rows[-2].update({"open": 101.0, "close": 103.5, "high": 103.6, "low": 100.9,
                     "kc_upper": 102.0})
    # 即時K：價格仍在上軌外
    rows[-1].update({"open": 103.5, "close": 103.8, "high": 103.9, "low": 103.4,
                     "kc_upper": 102.5})
    frame = pd.DataFrame(rows)
    decision = outer_strategy.aligned_entry(frame, 103.8)
    assert decision["action"] == "ENTER" and decision["side"] == "LONG"
