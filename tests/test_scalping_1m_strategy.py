import os
import numpy as np
import pandas as pd
import pytest
from core.services.chart_1m_scalper import (
    prepare_indicators_1m,
    generate_chart_signals,
    evaluate_scalping_1m_signal,
    is_scalping_1m_pullback_holding,
    plot_1m_strategy_chart,
)


def _build_dummy_1m_df(n=50, trend="UP"):
    if trend == "UP":
        base = np.linspace(100, 120, n)
    elif trend == "DOWN":
        base = np.linspace(120, 100, n)
    else:
        base = np.full(n, 100.0)

    dates = pd.date_range("2026-10-21 00:00", periods=n, freq="1min")
    return pd.DataFrame({
        "timestamp": dates,
        "open": base,
        "high": base + 0.5,
        "low": base - 0.5,
        "close": base + 0.2,
        "volume": 1000.0,
    })


def test_prepare_indicators_1m():
    df = _build_dummy_1m_df(30, "UP")
    out = prepare_indicators_1m(df)
    assert "ema_fast" in out.columns
    assert "ema_slow" in out.columns
    assert "fast_slope" in out.columns
    assert "ma_spread_pct" in out.columns
    assert "kc_upper" in out.columns
    assert "kc_lower" in out.columns
    assert "kc_basis" in out.columns
    assert "atr" in out.columns
    assert isinstance(out.index, pd.DatetimeIndex)


def test_generate_chart_signals_and_evaluation():
    # 構造強勢突破行情
    n = 35
    base = np.linspace(100, 110, n)
    dates = pd.date_range("2026-10-21 00:00", periods=n, freq="1min")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": base,
        "high": base + 0.3,
        "low": base - 0.3,
        "close": base,
        "volume": 500.0,
    })
    # 爆衝破軌
    df.loc[33, ["open", "high", "low", "close"]] = [115, 122, 114, 120]
    df.loc[34, ["open", "high", "low", "close"]] = [120, 126, 119, 125]

    ind_df = prepare_indicators_1m(df)
    sig_df = generate_chart_signals(ind_df, min_spread_pct=0.01)
    assert "sig_buy" in sig_df.columns
    assert "sig_sell" in sig_df.columns
    assert "sig_pullback" in sig_df.columns

    ok, reason, details = evaluate_scalping_1m_signal(df, "LONG", min_spread_pct=0.01)
    assert ok is True
    assert reason == "SCALPING_1M_LONG_BREAKOUT"
    assert details["ema_fast"] > details["ema_slow"]


def test_pullback_holding_logic():
    n = 35
    base = np.linspace(100, 110, n)
    dates = pd.date_range("2026-10-21 00:00", periods=n, freq="1min")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": base,
        "high": base + 0.5,
        "low": base - 0.5,
        "close": base,
        "volume": 500.0,
    })
    ind = prepare_indicators_1m(df)
    # 人工構造收盤價落在 fast 與 slow 之間
    fast_val = float(ind.iloc[-1]["ema_fast"])
    slow_val = float(ind.iloc[-1]["ema_slow"])
    assert fast_val > slow_val
    mid_price = (fast_val + slow_val) / 2
    df.loc[34, "close"] = mid_price

    holding = is_scalping_1m_pullback_holding(df, "LONG")
    assert holding is True


def test_plot_chart_output(tmp_path):
    df = _build_dummy_1m_df(30, "UP")
    ind = prepare_indicators_1m(df)
    sig = generate_chart_signals(ind)
    chart_path = str(tmp_path / "test_chart.png")
    plot_1m_strategy_chart(sig, title="Unit Test Chart", save_path=chart_path)
    assert os.path.exists(chart_path)
    assert os.path.getsize(chart_path) > 0
