import math
import time

import pandas as pd
import pytest

from core.gates.kc_overall_structure_gate import evaluate_entry_signal
from core.meme_coin_strategy_config import (
    DEFAULT_MEME_PARAMS,
    MemeCoinParams,
    get_meme_params,
)


def test_meme_coin_params_bounds_validation():
    assert DEFAULT_MEME_PARAMS.version == "2.0.0"
    assert DEFAULT_MEME_PARAMS.max_wick_ratio == 0.45
    assert DEFAULT_MEME_PARAMS.circuit_breaker_cooldown_sec == 420
    with pytest.raises(ValueError, match="max_wick_ratio"):
        MemeCoinParams(max_wick_ratio=0.95)
    with pytest.raises(ValueError, match="circuit_breaker_cooldown_sec"):
        MemeCoinParams(circuit_breaker_cooldown_sec=10)


def test_get_meme_params_returns_symbol_config():
    assert get_meme_params("1000PEPE/USDT") == DEFAULT_MEME_PARAMS
    assert get_meme_params("龙虾/USDT") == DEFAULT_MEME_PARAMS
    assert get_meme_params(None) == DEFAULT_MEME_PARAMS


def _build_frames(side="LONG", *, prev_wick_ratio=0.1, early_impulse=False):
    now = time.time()
    current_minute = math.floor(now * 1000 / 60_000) * 60_000
    direction = 1 if side == "LONG" else -1
    rows = []
    for index in range(16):
        middle = 100.0 + direction * index * 0.2
        opening = middle + (0.1 if side == "SHORT" else 0.2)
        close = opening - 0.2 if side == "SHORT" else opening + 0.1
        high = max(opening, close) + 0.05
        low = min(opening, close) - 0.05
        if index == 14 and prev_wick_ratio > 0.5:
            if side == "LONG":
                high = max(opening, close) + 2.0
            else:
                low = min(opening, close) - 2.0
        if index == 15 and early_impulse:
            close = opening + direction * 0.5
            high = max(opening, close) + 0.05
            low = min(opening, close) - 0.05
        rows.append({
            "timestamp": current_minute - (15 - index) * 60_000,
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
            "kc_middle": middle,
            "kc_upper": 110.0 + direction * index * 0.2,
            "kc_lower": 90.0 + direction * index * 0.2,
            "atr": 1.0,
            "ma5": middle + (0.4 if side == "LONG" else -0.4),
            "ma15": middle + (0.2 if side == "LONG" else -0.2),
            "is_closed": index < 15,
        })
    ltf = pd.DataFrame(rows)
    ltf.attrs.update({"timeframe_ms": 60_000, "snapshot_ms": now * 1000})

    current_quarter_hour = math.floor(now * 1000 / 900_000) * 900_000
    htf = pd.DataFrame([
        {
            "timestamp": current_quarter_hour - 900_000,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "kc_middle": 100.0, "atr": 1.0, "is_closed": True,
        },
        {
            "timestamp": current_quarter_hour,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.1,
            "kc_middle": 100.0 + direction * 0.1, "atr": 1.0, "is_closed": False,
        },
    ])
    htf.attrs.update({"timeframe_ms": 900_000, "snapshot_ms": now * 1000})
    return ltf, htf, float(ltf.iloc[-1]["close"]), now


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_meme_entry_blocks_excessive_wick_but_isolates_mainstream(side):
    ltf, htf, quote, now = _build_frames(side, prev_wick_ratio=0.8)
    meme_res = evaluate_entry_signal(side, ltf, htf, quote, symbol="1000PEPE/USDT", now=now)
    assert not meme_res["allowed"]
    assert meme_res["reason"] == "BLOCKED_ENTRY_EXCESSIVE_WICK"

    btc_res = evaluate_entry_signal(side, ltf, htf, quote, symbol="BTC/USDT", now=now)
    assert btc_res["allowed"]


def test_meme_entry_distinguishes_mode_a_and_mode_b():
    ltf_a, htf_a, quote_a, now_a = _build_frames("LONG", early_impulse=True)
    res_a = evaluate_entry_signal("LONG", ltf_a, htf_a, quote_a, symbol="龙虾/USDT", now=now_a)
    assert res_a["allowed"]
    assert res_a["entry_mode"] == "MODE_A"

    ltf_b, htf_b, quote_b, now_b = _build_frames("LONG", early_impulse=False)
    res_b = evaluate_entry_signal("LONG", ltf_b, htf_b, quote_b, symbol="龙虾/USDT", now=now_b)
    assert res_b["allowed"]
    assert res_b["entry_mode"] == "MODE_B"
