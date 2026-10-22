"""
反側 KC 外軌擊穿：大週期同向時須已收線 >= 0.5 ATR 確認（2026-10-22 使用者授權方案 A）。

回歸案例：龙虾/USDT 2026-10-22 09:01:17 多單被盤中毛刺（僅低於下軌約 0.0x ATR）
以 OPPOSITE_KC_BAND_BREACH 洗出場，下一根即收回軌內，09:15 又以更高價重開。
"""
import pandas as pd

from core.services.data_pipeline import DataPipeline
from core.gates.unified_pipeline_gates import ExitGate

# 幣安 龙虾USDT 1m（08:02~09:00 已收線），最後一根為 09:01:17 盤中棒
_LOBSTER_0901 = [
    [0.02122, 0.02131, 0.02122, 0.02123], [0.02124, 0.02125, 0.02111, 0.02111], [0.02112, 0.02115, 0.02106, 0.02115],
    [0.02115, 0.02118, 0.02108, 0.02112], [0.02112, 0.02115, 0.02108, 0.02114], [0.02115, 0.02119, 0.02114, 0.02116],
    [0.02116, 0.02117, 0.02114, 0.02115], [0.02114, 0.02115, 0.02111, 0.02113], [0.02113, 0.02113, 0.0211, 0.02113],
    [0.02113, 0.02113, 0.02107, 0.0211], [0.02111, 0.02111, 0.02109, 0.0211], [0.02109, 0.02113, 0.02109, 0.0211],
    [0.02111, 0.02111, 0.02107, 0.02108], [0.02109, 0.02109, 0.02106, 0.02107], [0.02107, 0.0211, 0.02106, 0.0211],
    [0.0211, 0.0211, 0.02102, 0.02104], [0.02105, 0.02105, 0.021, 0.02102], [0.02102, 0.02105, 0.02099, 0.02103],
    [0.02103, 0.02105, 0.02098, 0.02102], [0.02102, 0.02102, 0.02094, 0.02095], [0.02096, 0.02096, 0.02088, 0.02092],
    [0.02092, 0.02097, 0.02092, 0.02097], [0.02096, 0.021, 0.02093, 0.021], [0.021, 0.02101, 0.02097, 0.021],
    [0.021, 0.021, 0.02095, 0.02097], [0.02096, 0.02096, 0.02091, 0.02092], [0.02092, 0.02092, 0.02087, 0.0209],
    [0.02091, 0.02092, 0.02087, 0.02087], [0.02088, 0.02088, 0.02086, 0.02087], [0.02086, 0.02087, 0.02083, 0.02086],
    [0.02086, 0.02092, 0.02086, 0.02092], [0.02091, 0.02101, 0.02091, 0.021], [0.02099, 0.02099, 0.02095, 0.02099],
    [0.02099, 0.02101, 0.02098, 0.02101], [0.02101, 0.02105, 0.02101, 0.02104], [0.02104, 0.02108, 0.02101, 0.02102],
    [0.02102, 0.02104, 0.02102, 0.02103], [0.02103, 0.02105, 0.02102, 0.02104], [0.02103, 0.02103, 0.021, 0.02102],
    [0.02102, 0.02105, 0.02101, 0.02103], [0.02102, 0.02105, 0.02101, 0.02104], [0.02103, 0.02104, 0.02102, 0.02103],
    [0.02103, 0.02103, 0.02102, 0.02103], [0.02102, 0.02104, 0.02102, 0.02104], [0.02104, 0.02106, 0.02104, 0.02106],
    [0.02105, 0.02109, 0.02105, 0.02109], [0.02108, 0.0211, 0.02107, 0.0211], [0.02109, 0.02109, 0.02106, 0.02108],
    [0.02108, 0.02116, 0.02108, 0.02114], [0.02114, 0.02121, 0.02114, 0.02119], [0.02118, 0.02119, 0.02114, 0.02116],
    [0.02117, 0.02124, 0.02116, 0.02124], [0.02123, 0.02129, 0.02123, 0.02126], [0.02125, 0.02128, 0.02121, 0.02123],
    [0.02123, 0.02125, 0.02121, 0.02123], [0.02123, 0.02123, 0.02116, 0.02116], [0.02117, 0.02118, 0.02116, 0.02116],
    [0.02116, 0.02117, 0.02113, 0.02113], [0.02112, 0.02113, 0.02106, 0.02106], [0.02106, 0.02106, 0.021, 0.02103],
]
_LONG_POS = {"side": "LONG", "entry_price": 0.021082, "qty": 1.0, "peak_roe": 0.0099}


def _frame(rows):
    return pd.DataFrame([[o, h, l, c, 1000.0] for o, h, l, c in rows],
                        columns=["open", "high", "low", "close", "volume"])


def _mirror(rows, pivot=0.042):
    """價格鏡像（p -> pivot - p），將多頭情境翻成對稱空頭情境。"""
    return [[pivot - o, pivot - l, pivot - h, pivot - c] for o, h, l, c in rows]


def test_lobster_0901_intrabar_wick_holds_when_macro_bullish():
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", _frame(_LOBSTER_0901),
                                           current_price=0.02103, position=dict(_LONG_POS))
    assert snap.macro_trend == "BULLISH"
    assert snap.current_price < snap.kc_lower  # 盤中確實毛刺擊穿下軌
    should_exit, reason, meta = ExitGate.evaluate(dict(_LONG_POS), snap)
    assert should_exit is False
    assert reason != "OPPOSITE_KC_BAND_BREACH"
    veto = meta["opposite_breach_veto"]
    assert veto["reason"] == "VETO_OPPOSITE_BREACH_MACRO_ALIGNED_UNCONFIRMED"
    assert veto["closed_bar_breach"]["confirmed"] is False


def test_long_exits_when_closed_bar_confirms_deep_breach_even_if_macro_bullish():
    rows = [list(r) for r in _LOBSTER_0901]
    # 上一根已收線深跌收在下軌外（> 0.5 ATR），盤中仍在外
    rows[-2] = [0.02112, 0.02113, 0.02095, 0.02096]
    rows[-1] = [0.02096, 0.02097, 0.02093, 0.02094]
    snap = DataPipeline.calculate_snapshot("X", _frame(rows), current_price=0.02094,
                                           position=dict(_LONG_POS))
    snap.macro_trend = "BULLISH"
    confirm = ExitGate._closed_bar_opposite_breach(snap.raw_df, "LONG")
    assert confirm["confirmed"] is True
    should_exit, reason, _ = ExitGate.evaluate(dict(_LONG_POS), snap)
    assert should_exit is True and reason == "OPPOSITE_KC_BAND_BREACH"


def test_long_intrabar_breach_exits_immediately_when_macro_not_bullish():
    snap = DataPipeline.calculate_snapshot("X", _frame(_LOBSTER_0901), current_price=0.02103,
                                           position=dict(_LONG_POS))
    for trend in ("NEUTRAL", "BEARISH"):
        snap.macro_trend = trend
        should_exit, reason, _ = ExitGate.evaluate(dict(_LONG_POS), snap)
        assert should_exit is True and reason == "OPPOSITE_KC_BAND_BREACH"


def test_short_mirror_holds_on_unconfirmed_wick_when_macro_bearish():
    rows = _mirror(_LOBSTER_0901)
    pos = {"side": "SHORT", "entry_price": 0.042 - 0.021082, "qty": 1.0, "peak_roe": 0.0099}
    snap = DataPipeline.calculate_snapshot("X", _frame(rows), current_price=0.042 - 0.02103,
                                           position=dict(pos))
    assert snap.macro_trend == "BEARISH"
    assert snap.current_price > snap.kc_upper
    should_exit, reason, meta = ExitGate.evaluate(dict(pos), snap)
    assert should_exit is False
    assert "opposite_breach_veto" in meta
    snap.macro_trend = "NEUTRAL"
    should_exit, reason, _ = ExitGate.evaluate(dict(pos), snap)
    assert should_exit is True and reason == "OPPOSITE_KC_BAND_BREACH"


def test_invalid_closed_bar_data_falls_back_to_immediate_exit():
    md = {"current_price": 0.98, "atr": 0.01, "kc_upper": 1.02, "kc_lower": 0.99,
          "kc_middle": 1.0, "macro_trend": "BULLISH", "raw_df": None}
    should_exit, reason, _ = ExitGate.evaluate({"side": "LONG", "entry_price": 1.0}, md)
    assert should_exit is True and reason == "OPPOSITE_KC_BAND_BREACH"
