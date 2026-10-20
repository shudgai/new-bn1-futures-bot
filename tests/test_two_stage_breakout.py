"""
測試破軌兩段式進場服務 (TwoStageBreakoutService)
驗證：
1. 強突破 (BREAK_STRONG): 一根就進，開 50% 倉位，初始止損 0.3*ATR
2. 普通突破 (BREAK_CONFIRM): 等第二根同色站在軌外確認，開 100% 倉位
3. 強突破加倉 (BREAK_STRONG_ADD): 持有 50% 倉位次根繼續同色在軌外，補到 100%
4. 多空對稱性
"""

import pandas as pd
import pytest
from core.services.two_stage_breakout_service import (
    TwoStageBreakoutService,
    BREAK_STRONG,
    BREAK_CONFIRM,
    BREAK_STRONG_ADD,
)


def _make_base_frame(rows: int = 25, base_price: float = 100.0) -> pd.DataFrame:
    data = []
    p = base_price
    for i in range(rows):
        # 預設平穩走勢，平均實體約 0.2，ATR 約 0.5
        c_open = p
        c_close = p + 0.2
        c_high = p + 0.3
        c_low = p - 0.1
        data.append({
            "timestamp": 1000 + i * 180_000,
            "open": c_open,
            "high": c_high,
            "low": c_low,
            "close": c_close,
            "kc_upper": p + 1.0,
            "kc_lower": p - 1.0,
            "kc_middle": p,
            "atr": 0.5,
            "is_closed": True,
        })
        p += 0.1
    return pd.DataFrame(data)


def test_break_strong_long_success():
    """驗證做多強突破一根就進"""
    df = _make_base_frame(rows=25, base_price=100.0)
    # 前一根在軌道內 (close=101.5, kc_upper=102.0)
    df.loc[df.index[-2], "open"] = 101.3
    df.loc[df.index[-2], "close"] = 101.5
    df.loc[df.index[-2], "kc_upper"] = 102.0
    df.loc[df.index[-2], "kc_lower"] = 100.0

    # 當前根強突破：
    # 前 10 根 avg_body 約 0.2
    # 設置當前根：open=101.8, close=102.4 (實體 0.6 >= 1.5 * 0.2)
    # high=102.5, low=101.7 (rng=0.8, body/rng = 0.6/0.8 = 75% >= 60%)
    # 收盤靠近高點: (high - close) = 0.1 <= 0.25 * 0.8 = 0.20
    # kc_upper = 102.1 (close - kc_upper = 0.3 <= 1.0 * atr(0.5))
    # 通道擴張: kc_width_curr = 102.1 - 99.9 = 2.2 > kc_width[-4]=2.0
    df.loc[df.index[-4], "kc_upper"] = 102.0
    df.loc[df.index[-4], "kc_lower"] = 100.0
    df.loc[df.index[-1], "open"] = 101.8
    df.loc[df.index[-1], "close"] = 102.4
    df.loc[df.index[-1], "high"] = 102.5
    df.loc[df.index[-1], "low"] = 101.7
    df.loc[df.index[-1], "kc_upper"] = 102.1
    df.loc[df.index[-1], "kc_lower"] = 99.9
    df.loc[df.index[-1], "atr"] = 0.5

    sig = TwoStageBreakoutService.evaluate(df, "LONG", mode="HYBRID")
    assert sig is not None
    assert sig["entry_source"] == BREAK_STRONG
    assert sig["size_ratio"] == 0.50
    # 止損在突破棒另一端外加 0.3 * ATR: 101.7 - 0.3 * 0.5 = 101.55
    assert pytest.approx(sig["initial_sl"], 0.001) == 101.55


def test_break_strong_rejected_if_not_expanding():
    """通道未擴張時，強突破不成立"""
    df = _make_base_frame(rows=25, base_price=100.0)
    df.loc[df.index[-2], "kc_upper"] = 102.0
    df.loc[df.index[-2], "close"] = 101.5

    # 通道收縮: 前面寬度 3.0，現在只有 2.0
    df.loc[df.index[-4], "kc_upper"] = 103.0
    df.loc[df.index[-4], "kc_lower"] = 100.0
    df.loc[df.index[-1], "open"] = 101.8
    df.loc[df.index[-1], "close"] = 102.4
    df.loc[df.index[-1], "high"] = 102.5
    df.loc[df.index[-1], "low"] = 101.7
    df.loc[df.index[-1], "kc_upper"] = 102.1
    df.loc[df.index[-1], "kc_lower"] = 100.1
    df.loc[df.index[-1], "atr"] = 0.5

    sig = TwoStageBreakoutService.evaluate(df, "LONG", mode="HYBRID")
    # 通道未擴張，不能一根進場
    assert sig is None


def test_break_confirm_long():
    """普通突破：第二根同色仍站在軌外時觸發 BREAK_CONFIRM"""
    df = _make_base_frame(rows=25, base_price=100.0)
    # 前前根在軌內
    df.loc[df.index[-3], "close"] = 101.8
    df.loc[df.index[-3], "kc_upper"] = 102.0

    # 前一根破軌收陽 (out_prev=True, is_prev_color=True)
    df.loc[df.index[-2], "open"] = 101.9
    df.loc[df.index[-2], "close"] = 102.3
    df.loc[df.index[-2], "kc_upper"] = 102.1
    df.loc[df.index[-2], "low"] = 101.8

    # 當前根繼續在軌外收陽 (out_now=True, is_color=True)
    df.loc[df.index[-1], "open"] = 102.3
    df.loc[df.index[-1], "close"] = 102.6
    df.loc[df.index[-1], "high"] = 102.7
    df.loc[df.index[-1], "low"] = 102.2
    df.loc[df.index[-1], "kc_upper"] = 102.2
    df.loc[df.index[-1], "kc_lower"] = 100.0
    df.loc[df.index[-1], "atr"] = 0.5

    sig = TwoStageBreakoutService.evaluate(df, "LONG", mode="HYBRID")
    assert sig is not None
    assert sig["entry_source"] == BREAK_CONFIRM
    assert sig["size_ratio"] == 1.0


def test_break_strong_add_scale_in():
    """若已持有 BREAK_STRONG 倉位，次根同色在軌外觸發加倉 BREAK_STRONG_ADD"""
    df = _make_base_frame(rows=25, base_price=100.0)
    df.loc[df.index[-1], "open"] = 102.3
    df.loc[df.index[-1], "close"] = 102.6
    df.loc[df.index[-1], "high"] = 102.7
    df.loc[df.index[-1], "low"] = 102.2
    df.loc[df.index[-1], "kc_upper"] = 102.2
    df.loc[df.index[-1], "atr"] = 0.5

    active_pos = {
        "side": "LONG",
        "entry_source": BREAK_STRONG,
        "has_scaled_in": False,
    }

    sig = TwoStageBreakoutService.evaluate(df, "LONG", position=active_pos, mode="HYBRID")
    assert sig is not None
    assert sig["entry_source"] == BREAK_STRONG_ADD
    assert sig["is_scale_in"] is True
    assert sig["size_ratio"] == 0.50


def test_break_strong_short_symmetry():
    """驗證做空強突破對稱性"""
    df = _make_base_frame(rows=25, base_price=100.0)
    # 前一根在軌內 (close=98.5, kc_lower=98.0)
    df.loc[df.index[-2], "open"] = 98.7
    df.loc[df.index[-2], "close"] = 98.5
    df.loc[df.index[-2], "kc_upper"] = 100.0
    df.loc[df.index[-2], "kc_lower"] = 98.0

    # 當前根強突破跌破下軌收陰
    df.loc[df.index[-4], "kc_upper"] = 100.0
    df.loc[df.index[-4], "kc_lower"] = 98.0
    df.loc[df.index[-1], "open"] = 98.2
    df.loc[df.index[-1], "close"] = 97.6
    df.loc[df.index[-1], "high"] = 98.3
    df.loc[df.index[-1], "low"] = 97.5
    df.loc[df.index[-1], "kc_upper"] = 100.1
    df.loc[df.index[-1], "kc_lower"] = 97.9
    df.loc[df.index[-1], "atr"] = 0.5

    sig = TwoStageBreakoutService.evaluate(df, "SHORT", mode="HYBRID")
    assert sig is not None
    assert sig["entry_source"] == BREAK_STRONG
    assert sig["size_ratio"] == 0.50
    # 止損在突破棒另一端外加 0.3 * ATR: 98.3 + 0.3 * 0.5 = 98.45
    assert pytest.approx(sig["initial_sl"], 0.001) == 98.45


def test_break_confirm_opp_small_body_long():
    """驗證做多普通突破第二根反色但微弱實體 (<=0.6*avg_body) 放行開 50% 倉位"""
    from core.services.two_stage_breakout_service import BREAK_CONFIRM_OPP
    df = _make_base_frame(rows=25, base_price=100.0)
    # 前前根在軌內
    df.loc[df.index[-3], "close"] = 101.8
    df.loc[df.index[-3], "kc_upper"] = 102.0

    # 前一根破軌收陽 (out_prev=True, is_prev_color=True)
    df.loc[df.index[-2], "open"] = 101.9
    df.loc[df.index[-2], "close"] = 102.3
    df.loc[df.index[-2], "kc_upper"] = 102.1

    # 當前根仍在軌外 (close=102.25 > kc_upper=102.20)，但反色收陰 (open=102.30, close=102.25, body=0.05)
    # 前 10 根 avg_body 約 0.20，body 0.05 <= 0.6 * 0.20 = 0.12
    df.loc[df.index[-1], "open"] = 102.30
    df.loc[df.index[-1], "close"] = 102.25
    df.loc[df.index[-1], "high"] = 102.35
    df.loc[df.index[-1], "low"] = 102.22
    df.loc[df.index[-1], "kc_upper"] = 102.20
    df.loc[df.index[-1], "atr"] = 0.5

    sig = TwoStageBreakoutService.evaluate(df, "LONG", mode="HYBRID")
    assert sig is not None
    assert sig["entry_source"] == BREAK_CONFIRM_OPP
    assert sig["size_ratio"] == 0.50


def test_break_confirm_opp_large_body_rejected():
    """驗證做多普通突破第二根反色且大實體 (>0.6*avg_body) 拒絕放行"""
    df = _make_base_frame(rows=25, base_price=100.0)
    df.loc[df.index[-3], "close"] = 101.8
    df.loc[df.index[-3], "kc_upper"] = 102.0

    df.loc[df.index[-2], "open"] = 101.9
    df.loc[df.index[-2], "close"] = 102.3
    df.loc[df.index[-2], "kc_upper"] = 102.1

    # 當前根反色大陰棒 (open=102.40, close=102.22, body=0.18 > 0.6 * 0.20)
    df.loc[df.index[-1], "open"] = 102.40
    df.loc[df.index[-1], "close"] = 102.22
    df.loc[df.index[-1], "kc_upper"] = 102.20
    df.loc[df.index[-1], "atr"] = 0.5

    sig = TwoStageBreakoutService.evaluate(df, "LONG", mode="HYBRID")
    assert sig is None

