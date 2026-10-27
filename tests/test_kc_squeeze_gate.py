"""
tests/test_kc_squeeze_gate.py
=============================
驗證【關鍵風控】KC 通道擠壓/狹幅盤整濾網 (KC Squeeze Gate) 核心規範：
1. 通道頻寬比率定義：kc_width_ratio = (kc_upper - kc_lower) / kc_middle
2. 盤整鎖定條件 (SQUEEZE_LOCK)：
   - kc_width_ratio < 0.015 (上下軌間距小於中軌的 1.5%)，或
   - current_atr < 0.7 * avg_atr_20 (當前 ATR 顯著低於 20 根平均波動率)
   【強制一票否決】所有常規 MA3/MA15 交叉開倉 (BLOCKED_BY_KC_SQUEEZE_LOCK)！
3. 解鎖條件 (Breakout Expansion)：
   只有當為破軌類訊號 (BREAKOUT) 且帶量突破外軌 (curr_price >= kc_upper) 時放行！
4. 正常趨勢寬度時常規開倉正常放行。
"""
import pytest
import pandas as pd
from core.gates.central_entry_gate import CentralEntryGate


def _make_squeeze_df(
    kc_middle=100.0,
    kc_width=1.0, # 1.0 / 100.0 = 0.01 (1.0% < 1.5%) -> SQUEEZE
    atr=0.25,
    avg_atr=0.25,
    curr_price=100.5,
    ma3=100.5,
    ma5=100.2,
    ma15=99.8,
):
    rows = []
    base_time = 1_000_000
    for i in range(25):
        # 讓中軌緩慢向上 (避免觸發 VETO_FLAT_MA3_OR_KC_MIDLINE)
        mid = kc_middle - (25 - i) * 0.05
        w = kc_width * 0.92 if i < 24 else kc_width
        rows.append({
            "timestamp": base_time + i * 60_000,
            "open": mid,
            "high": mid + avg_atr,
            "low": mid - avg_atr,
            "close": mid,
            "atr": avg_atr,
            "kc_upper": mid + w / 2.0,
            "kc_middle": mid,
            "kc_lower": mid - w / 2.0,
            "ma3": mid,
            "ma5": mid,
            "ma15": mid - 0.2,
            "volume": 1000.0,
            "is_closed": True,
        })
    # 倒數第二根
    rows[-2]["ma15"] = ma15 - 0.1
    # 最後一根
    rows[-1]["close"] = curr_price
    rows[-1]["high"] = max(curr_price, kc_middle + atr)
    rows[-1]["low"] = min(curr_price, kc_middle - atr)
    rows[-1]["atr"] = atr
    rows[-1]["ma3"] = ma3
    rows[-1]["ma5"] = ma5
    rows[-1]["ma15"] = ma15
    rows[-1]["volume"] = 1500.0
    rows[-1]["kc_upper"] = kc_middle + kc_width / 2.0
    rows[-1]["kc_middle"] = kc_middle
    rows[-1]["kc_lower"] = kc_middle - kc_width / 2.0

    df = pd.DataFrame(rows)
    df.attrs["macro_trend"] = "BULLISH"
    return df


def test_squeeze_lock_rejects_regular_ma_cross_when_width_ratio_below_1_5pct():
    """1. 通道寬度小於 1.5% 時，強制一票否決常規 MA 交叉開多訊號"""
    # kc_middle=100.0, kc_width=1.0 -> ratio=0.01 (1.0% < 1.5%)
    df = _make_squeeze_df(kc_middle=100.0, kc_width=1.0, atr=0.25, avg_atr=0.25, curr_price=100.2)

    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=100.2,
        klines_or_snapshot=df,
        source="CROSS_MID_MA3_MA15",
    )

    assert res.passed is False
    assert res.reason == "BLOCKED_BY_KC_SQUEEZE_LOCK"
    assert res.details.get("market_regime") == "MARKET_SQUEEZE"
    assert res.details.get("kc_width_ratio") < 0.015


def test_squeeze_lock_rejects_when_atr_below_70pct_of_20bar_avg():
    """2. 當前 ATR 小於 20 根平均 ATR 的 70% 時，強制一票否決"""
    # kc_width=3.0 -> ratio=3.0% (寬度合格)，但當前 atr=0.10 < 0.7 * avg_atr(0.25)=0.175
    df = _make_squeeze_df(kc_middle=100.0, kc_width=3.0, atr=0.10, avg_atr=0.25, curr_price=100.2)

    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=100.2,
        klines_or_snapshot=df,
        source="CROSS_MID_MA3_MA15",
    )

    assert res.passed is False
    assert res.reason == "BLOCKED_BY_KC_SQUEEZE_LOCK"
    assert res.details.get("market_regime") == "MARKET_SQUEEZE"


def test_squeeze_unlocks_on_breakout_expansion_outside_band():
    """3. 處於 SQUEEZE 時，若為破軌突破 (BREAKOUT) 且突破外軌，成功解鎖放行"""
    # kc_width=1.0 (1.0% 處於擠壓)，但為 BREAKOUT 且價格突破上軌 (100.6 > 100.5)
    df = _make_squeeze_df(kc_middle=100.0, kc_width=1.0, atr=0.25, avg_atr=0.25, curr_price=100.6, ma3=100.7, ma5=100.6)

    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=100.6,
        klines_or_snapshot=df,
        source="CONFIRMED_2BAR_BREAKOUT",
    )

    # 必須成功通過 SQUEEZE 審查 (checks["kc_squeeze_gate"]["status"] == "PASS")
    assert res.checks.get("kc_squeeze_gate", {}).get("status") == "PASS"
    assert res.checks.get("kc_squeeze_gate", {}).get("breakout_unlocked") is True


def test_normal_trending_width_allows_regular_ma_cross():
    """4. 正常通道頻寬 (比率 3.0% >= 1.5% 且 ATR 飽滿)，常規交叉開倉順利放行"""
    df = _make_squeeze_df(kc_middle=100.0, kc_width=3.0, atr=0.25, avg_atr=0.25, curr_price=100.2, ma5=100.2, ma15=99.2)

    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=100.2,
        klines_or_snapshot=df,
        source="CROSS_MID_MA3_MA15",
    )

    assert res.checks.get("kc_squeeze_gate", {}).get("status") == "PASS"
    assert res.passed is True
