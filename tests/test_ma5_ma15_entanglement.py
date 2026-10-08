import pandas as pd
import pytest

from core.services.ma5_chop_gate import ma5_ma15_entanglement_problem


def market():
    rows = [dict(timestamp=(i+1)*60000, close=100., ma5=100., ma15=100.,
                 atr=1., is_closed=True) for i in range(15)]
    rows.append(dict(timestamp=960000, close=100., ma5=100., ma15=100.,
                     atr=1., is_closed=False))
    return pd.DataFrame(rows)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("gap", [.099, .1, .101])
def test_three_closed_gap_threshold(symbol, side, gap):
    f = market()
    f.attrs["symbol"] = symbol
    f.loc[:14, "ma5"] = 100.+gap
    result = ma5_ma15_entanglement_problem(f, 100., side)
    assert result == ("BLOCKED_MA5_MA15_ENTANGLED" if gap <= .1 else None)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("quote_change", [.70, .75, .76, 1.])
def test_live_both_directional_averages_must_separate_strictly(symbol, side, quote_change):
    f = market()
    sign = 1 if side == "LONG" else -1
    result = ma5_ma15_entanglement_problem(f, 100.+sign*quote_change, side)
    assert result == (None if quote_change > .75 else "BLOCKED_MA5_MA15_ENTANGLED")
    assert ma5_ma15_entanglement_problem(f, 100.-sign*quote_change, side) == "BLOCKED_MA5_MA15_ENTANGLED"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_only_consecutive_three_entangled_bars_block(side):
    f = market()
    f.loc[12, "ma5"] = 100.2
    assert ma5_ma15_entanglement_problem(f, 100., side) is None
    f.loc[12, "ma5"] = 100.
    assert ma5_ma15_entanglement_problem(f, 100., side) == "BLOCKED_MA5_MA15_ENTANGLED"


@pytest.mark.parametrize("fault", ["atr", "ma15", "short", "quote"])
def test_invalid_entanglement_data_blocks(fault):
    f = market()
    quote = 100.
    if fault == "atr":
        f.loc[14, "atr"] = 0.
    elif fault == "ma15":
        f.loc[14, "ma15"] = float("nan")
    elif fault == "short":
        f = f.tail(5)
    else:
        quote = float("nan")
    assert ma5_ma15_entanglement_problem(f, quote, "LONG") == "WAIT_MA5_MA15_DATA"
