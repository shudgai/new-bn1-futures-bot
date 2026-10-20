"""
測試 3 分鐘超短線（Scalping / 短線動量）架構規則：
1. 正常回踩（盤中影線刺破但在 EMA13 之上收線）➔ 嚴禁平倉，持倉等待
2. 趨勢衰退 1（3M 收盤實體跌破 EMA13）➔ 果斷平倉
3. 趨勢衰退 2（均線死叉/走平 + 跌入 KC 通道中軌）➔ 果斷平倉
4. 破軌開倉嚴格需第一根實體破軌，第二根收盤同色在 KC 外才開倉
"""

import pandas as pd
import pytest
from core.services.strategies.wild_swing_strategy import WildSwingStrategy
from core.gates.breakout_continuation_entry_gate import BreakoutContinuationEntryGate, CONFIRMED_2BAR_BREAKOUT


def _create_candles(count: int = 30, base: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    records = []
    p = base
    for i in range(count):
        p += step
        records.append({
            "timestamp": 1000 + i * 180_000,
            "open": p - 0.2,
            "high": p + 0.4,
            "low": p - 0.3,
            "close": p,
            "ma5": p + 0.2,
            "ema5": p + 0.2,
            "_wild_ma5": p + 0.2,
            "ema13": p - 0.5,
            "ma15": p - 0.5,
            "kc_middle": p - 1.0,
            "kc_upper": p + 2.0,
            "kc_lower": p - 3.0,
            "atr": 1.0,
            "is_closed": True,
        })
    df = pd.DataFrame(records)
    df.attrs["timeframe_ms"] = 180_000.0
    return df


def test_normal_pullback_not_exit():
    """正常回踩（盤中影線刺破 EMA13，但收盤穩在 EMA13 之上）➔ 嚴禁平倉"""
    df = _create_candles(35)
    strat = WildSwingStrategy()
    pos = {"side": "LONG", "entry_price": 95.0, "amount": 10.0}

    # 模擬最後一根回踩：low 刺穿 ema13，但收盤價仍在 ema13 之上
    ema13 = df.iloc[-1]["ema13"]
    df.loc[df.index[-1], "low"] = ema13 - 0.3   # 盤中刺破
    df.loc[df.index[-1], "close"] = ema13 + 0.2 # 收盤收在 ema13 之上

    decision = strat.evaluate_exit("TESTUSDT", pos, df, quote=df.iloc[-1]["close"])
    assert decision is None  # 正常回踩，持倉等待，嚴禁平倉！


def test_trend_decay_signal_1_close_below_ema13():
    """趨勢衰退訊號 1：3M 收盤實體跌破 EMA13 ➔ 果斷平倉"""
    df = _create_candles(35)
    strat = WildSwingStrategy()
    pos = {"side": "LONG", "entry_price": 95.0, "amount": 10.0}

    # 模擬實體跌破 EMA13
    ema13 = df.iloc[-1]["ema13"]
    df.loc[df.index[-1], "close"] = ema13 - 0.1  # 收盤實體破 EMA13

    decision = strat.evaluate_exit("TESTUSDT", pos, df, quote=df.iloc[-1]["close"])
    assert decision is not None
    assert decision["action"] == "CLOSE_LONG"
    assert decision["type"] == "EXIT_BY_EMA13_BREAKDOWN"


def test_trend_decay_signal_2_dead_cross_and_kc_mid_breakdown():
    """趨勢衰退訊號 2：均線死叉/走平 + 摜破 KC 中軌 ➔ 果斷平倉"""
    df = _create_candles(35)
    strat = WildSwingStrategy()
    pos = {"side": "LONG", "entry_price": 95.0, "amount": 10.0}

    kc_mid = df.iloc[-1]["kc_middle"]
    # 均線死叉且價格摜破 KC 中軌
    df.loc[df.index[-1], "ma5"] = df.iloc[-1]["ema13"] - 0.2  # ema5 <= ema13 (死叉)
    df.loc[df.index[-1], "close"] = kc_mid - 0.2              # 摜破 KC 中軌

    decision = strat.evaluate_exit("TESTUSDT", pos, df, quote=df.iloc[-1]["close"])
    assert decision is not None
    assert decision["action"] == "CLOSE_LONG"
    assert decision["type"] in ("EXIT_BY_DEAD_CROSS_KC_MID_BREAKDOWN", "EXIT_BY_EMA13_BREAKDOWN")


def test_short_decay_signal_1_close_above_ema13():
    """空單趨勢衰退訊號 1：3M 收盤實體站上 EMA13 ➔ 果斷平倉"""
    df = _create_candles(35)
    strat = WildSwingStrategy()
    pos = {"side": "SHORT", "entry_price": 120.0, "amount": 10.0}

    # 空單模擬收盤站上 EMA13
    ema13 = df.iloc[-1]["ema13"]
    df.loc[df.index[-1], "close"] = ema13 + 0.2

    decision = strat.evaluate_exit("TESTUSDT", pos, df, quote=df.iloc[-1]["close"])
    assert decision is not None
    assert decision["action"] == "CLOSE_SHORT"
    assert decision["type"] == "EXIT_BY_EMA13_BREAKOUT"


def test_two_bar_closed_breakout_entry():
    """破軌開倉：第一根實體破軌，第二根收盤同色且在 KC 外才開倉"""
    df = _create_candles(35)
    # 設定前兩根：第一根突破上軌收陽，第二根繼續收陽在軌外
    u1 = df.iloc[-2]["kc_upper"]
    df.loc[df.index[-2], "open"] = u1 - 0.5
    df.loc[df.index[-2], "close"] = u1 + 0.3  # 第一根突破收陽
    df.loc[df.index[-2], "high"] = u1 + 0.5
    df.loc[df.index[-2], "low"] = u1 - 0.6

    u2 = df.iloc[-1]["kc_upper"]
    df.loc[df.index[-1], "open"] = u2 + 0.2
    df.loc[df.index[-1], "close"] = u2 + 0.8  # 第二根同色收陽在軌外
    df.loc[df.index[-1], "high"] = u2 + 1.0
    df.loc[df.index[-1], "low"] = u2 + 0.1

    closed_klines = df.to_dict("records")
    sig = BreakoutContinuationEntryGate.evaluate_pure_math(
        closed_klines,
        df["kc_upper"].tolist(),
        df["kc_lower"].tolist(),
        df["kc_middle"].tolist(),
        df["ma5"].tolist(),
        df["ma15"].tolist(),
    )
    assert sig is not None
    assert sig["type"] == CONFIRMED_2BAR_BREAKOUT
    assert sig["action"] == "OPEN_LONG"
    assert sig.get("is_intrabar") is not True  # 嚴格收線確認，非盤中即時價！


def test_breakout_second_bar_opposite_color_blocked():
    """破軌第二根若收反色陰線（如龍蝦 17:58），100% 絕對禁止開多！"""
    from core.gates.trend_riding_strategy import can_open_position
    from core.services.ema_cross_entry_service import EmaCrossEntryService

    df = _create_candles(35)
    # 第一根 (index -2)：突破上軌收陽線
    u1 = df.iloc[-2]["kc_upper"]
    df.loc[df.index[-2], "open"] = u1 - 0.5
    df.loc[df.index[-2], "close"] = u1 + 0.3
    df.loc[df.index[-2], "high"] = u1 + 0.5
    df.loc[df.index[-2], "low"] = u1 - 0.6

    # 第二根 (index -1)：在軌外但收反向陰線 (open=u2+0.8, close=u2+0.2, close < open)
    u2 = df.iloc[-1]["kc_upper"]
    df.loc[df.index[-1], "open"] = u2 + 0.8
    df.loc[df.index[-1], "close"] = u2 + 0.2
    df.loc[df.index[-1], "high"] = u2 + 1.0
    df.loc[df.index[-1], "low"] = u2 + 0.1

    # 1. 破軌純數學 Gate 必須拒絕 (回傳 None)
    closed_klines = df.to_dict("records")
    sig = BreakoutContinuationEntryGate.evaluate_pure_math(
        closed_klines,
        df["kc_upper"].tolist(),
        df["kc_lower"].tolist(),
        df["kc_middle"].tolist(),
        df["ma5"].tolist(),
        df["ma15"].tolist(),
    )
    assert sig is None

    # 2. 開倉防禦門禁 can_open_position 必須行使一票否決
    can_open, gate_reason, _ = can_open_position(
        df=df,
        side="LONG",
        quote=float(df.iloc[-1]["close"]),
        symbol="龙虾/USDT",
    )
    assert can_open is False
    assert "BLOCKED_BREAKOUT_SECOND_BAR_NOT_SAME_COLOR" in gate_reason or "BEARISH_CANDLE" in gate_reason

    # 3. EMA 交叉服務亦必須阻斷，不得藉由交叉/補開繞過
    e_sig, e_reason = EmaCrossEntryService.evaluate_entry("龙虾/USDT", df, "LONG")
    assert e_sig is None
    assert any(w in e_reason for w in ("NO_CROSS", "NOT_SAME_COLOR", "BEARISH_CANDLE"))


def test_cap_surge_holding_not_exit_until_ema13_decay():
    """驗證 CAP/USDT 大暴拉噴發時，價格在 EMA13 之上絕不提前平倉（刪除 STRETCH_BOUNCE 早退）"""
    df = _create_candles(35, base=0.0680, step=0.0001)
    strat = WildSwingStrategy()
    pos = {"side": "LONG", "entry_price": 0.0685, "amount": 1000.0}

    # 模擬 CAP 沿著 KC 上軌暴拉：價格高達 0.0705，乖離 > 3*ATR，但 EMA5 和 EMA13 強勁向上且價格穩居 EMA13 之上
    df.loc[df.index[-1], "close"] = 0.0705
    df.loc[df.index[-1], "open"] = 0.0708  # 盤中一根微幅收跌的小陰十字
    df.loc[df.index[-1], "ema13"] = 0.0690  # 價格 0.0705 遠高於 EMA13 0.0690
    df.loc[df.index[-1], "ma15"] = 0.0690
    df.loc[df.index[-1], "kc_middle"] = 0.0688
    df.loc[df.index[-1], "kc_upper"] = 0.0700
    df.loc[df.index[-1], "atr"] = 0.0004   # 乖離 (0.0705 - 0.0690) = 0.0015 = 3.75 ATR

    # 執行平倉評估：必須為 None（死抱讓利潤奔跑，絕不提早賣飛！）
    decision = strat.evaluate_exit("CAP/USDT", pos, df, quote=0.0705)
    assert decision is None
