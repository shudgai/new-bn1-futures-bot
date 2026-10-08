import pandas as pd
import pytest

from core.services.entry_contract import evaluate_small_bridge_breakout


def candles(side, count):
    sign = 1 if side == "LONG" else -1
    bodies = [.2, sign * .2] + [-sign * .2] * count + [sign * 1.]
    rows = []
    for index, body in enumerate(bodies):
        rows.append(dict(timestamp=(index + 1)*60000, open=100., close=100.+body,
                         high=max(100., 100.+body)+.05,
                         low=min(100., 100.+body)-.05, atr=1.,
                         kc_lower=99.5, kc_upper=100.5,
                         is_closed=index < len(bodies)-1))
    return pd.DataFrame(rows)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("count", [1, 2, 7, 210])
def test_any_number_of_adjacent_bridges(symbol, side, count):
    f = candles(side, count)
    result = evaluate_small_bridge_breakout(f, f.iloc[-1].close)
    assert result["side"] == side
    assert result["small_bridge_count"] == count
    assert len(result["small_bridge_bar_ids"]) == count
    assert result["live_body_min_atr"] == .5


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["medium", "doji", "gap", "not_closed", "atr", "below_big"])
def test_interruption_or_invalid_data_rejects(side, fault):
    f = candles(side, 4)
    if fault == "medium":
        f.loc[3, "close"] = 100.4
        f.loc[3, "high"] = 100.45
    elif fault == "doji":
        f.loc[3, "close"] = 100.
    elif fault == "gap":
        f.loc[3, "timestamp"] += 1
    elif fault == "not_closed":
        f.loc[3, "is_closed"] = False
    elif fault == "atr":
        f.loc[2, "atr"] = float("nan")
    else:
        f.loc[len(f)-1, "close"] = 100.+(.499 if side == "LONG" else -.499)
        f["kc_lower"], f["kc_upper"] = 99.6, 100.4
    assert evaluate_small_bridge_breakout(f, f.iloc[-1].close) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_each_small_uses_its_immediately_previous_atr(side):
    f = candles(side, 2)
    f.loc[1, "atr"] = .4
    assert evaluate_small_bridge_breakout(f, f.iloc[-1].close) is None
    f.loc[1, "atr"] = .8
    assert evaluate_small_bridge_breakout(f, f.iloc[-1].close)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body_atr", [.499, .5, .501, .75])
def test_half_atr_live_threshold(symbol, side, body_atr):
    f = candles(side, 2)
    f.attrs["symbol"] = symbol
    sign = 1 if side == "LONG" else -1
    quote = 100. + sign * body_atr
    f["kc_lower"], f["kc_upper"] = 99.6, 100.4
    result = evaluate_small_bridge_breakout(f, quote)
    if body_atr < .5:
        assert result is None
    else:
        assert result["side"] == side
        assert result["live_body_min_atr"] == .5
        assert result["live_body_atr"] == pytest.approx(body_atr)
