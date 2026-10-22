"""
tests/test_trend_ride_and_entry_exit_timing.py

專門針對「new bn」多空開倉位置與平倉位置（太晚開倉、太早平倉、太晚平倉）之 QA 補充測試套件。
涵蓋技術邊際 (極小值、盤中毛刺、收線確認) 與商業邊際 (手續費磨損、單邊趨勢死抱、假突破追高攔截)。
"""
import pytest
import pandas as pd
import numpy as np
from core.gates.unified_pipeline_gates import EntryGate, ExitGate
from core.services.data_pipeline import DataPipeline, MarketDataSnapshot


def _build_trend_df(n_bars=30, start_price=0.0210, trend="DOWN", step=0.00003):
    """構造合成 K 棒序列"""
    bars = []
    curr = start_price
    for i in range(n_bars):
        if trend == "DOWN":
            o = curr
            c = curr - step
            h = o + (step * 0.3)
            l = c - (step * 0.3)
            curr = c
        elif trend == "UP":
            o = curr
            c = curr + step
            h = c + (step * 0.3)
            l = o - (step * 0.3)
            curr = c
        else: # FLAT
            o = curr
            c = curr + (step * 0.1 if i % 2 == 0 else -step * 0.1)
            h = max(o, c) + step * 0.2
            l = min(o, c) - step * 0.2
            curr = c
        bars.append({"timestamp": 1792630000000 + i * 60000, "open": o, "high": h, "low": l, "close": c, "volume": 1000.0})
    return pd.DataFrame(bars)


# ==============================================================================
# 業務場景 1：【平倉位置審查】單邊強勢下跌浪，價格受壓於 MA5，微小反抽嚴禁頻繁平空 (走到最後)
# ==============================================================================
def test_exit_timing_strong_downtrend_holds_through_minor_pullbacks():
    """
    [商業場景：防範手續費過度磨損與踏空大主浪]
    在單邊強勢主跌浪中，K 棒呈現 -45度陡峭下行，價格一路受壓於 MA5 關鍵防守線之下。
    盤中出現 1~2 根極微小的小陽線反抽 (但收盤價依然在 MA5 之下，反抽深度 < 0.35 ATR)。
    【預期商業行為】：
    系統必須強制 VETO_HOLD 死抱到底，絕不允許被微弱分時雜訊洗出場！
    徹底避免「平空 ➔ 追空 ➔ 平空 ➔ 追空」的手續費雙向割肉！
    """
    df = _build_trend_df(30, start_price=0.0210, trend="DOWN", step=0.00004)
    # 最後兩根：構造一根探底陰線，隨後一根微幅小紅棒 (收盤依然低於 MA5)
    c_prev = df.iloc[-2]
    ma5_val = df["close"].tail(5).mean()
    # 構造微弱小紅棒：收盤價仍小於 MA5，且反彈深度極微弱
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [c_prev["close"], c_prev["close"] + 0.00001, c_prev["close"] - 0.000005, c_prev["close"] + 0.000008]

    quote = df.iloc[-1]["close"]
    pos = {"symbol": "龙虾/USDT", "side": "SHORT", "entry_price": 0.0210, "qty": 10000.0, "leverage": 5.0}

    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote, position=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=quote)

    assert should_exit is False, f"單邊主跌浪中價格未突破 MA5，嚴禁頻繁平空！實際平倉原因: {reason}, meta: {meta}"
    assert reason == "VETO_HOLD"
    assert "MA5" in meta.get("cause", "") or "死抱" in meta.get("cause", "")


# ==============================================================================
# 業務場景 2：【平倉位置審查】單邊強勢主升浪，價格守穩於 MA5 之上，微小回踩嚴禁頻繁平多
# ==============================================================================
def test_exit_timing_strong_uptrend_holds_through_minor_dips():
    """
    [商業場景：防範主升浪半途被洗下車]
    在強勢單邊暴漲走勢中，價格沿著 KC 上軌狂飆，守穩於 MA5 之上。
    盤中出現小黑棒回踩 (但未跌破 MA5，回落深度 < 0.35 ATR)。
    【預期商業行為】：
    強制輸出 VETO_HOLD，不允許普通階梯或微型回踩提前出場，抱牢整波暴利！
    """
    df = _build_trend_df(30, start_price=0.0200, trend="UP", step=0.00004)
    c_prev = df.iloc[-2]
    # 構造微弱小黑棒：回踩但仍穩穩踩在 MA5 之上
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [c_prev["close"] + 0.00001, c_prev["close"] + 0.000015, c_prev["close"] - 0.000005, c_prev["close"] - 0.000002]

    quote = df.iloc[-1]["close"]
    pos = {"symbol": "龙虾/USDT", "side": "LONG", "entry_price": 0.0200, "qty": 10000.0, "leverage": 5.0}

    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote, position=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=quote)

    assert should_exit is False, f"強勢主升浪守穩 MA5 之上，嚴禁提前平多！實際原因: {reason}"
    assert reason == "VETO_HOLD"


# ==============================================================================
# 業務場景 3：【平倉位置審查】走勢真正「走到最後」：實體收復突破 MA5 且反轉確立，及時獲利了結
# ==============================================================================
def test_exit_timing_reclaims_ma5_at_trend_exhaustion():
    """
    [商業場景：波段走到最後，放行落袋為安]
    空單持倉在經歷了一整段主跌浪後，底端出現放量大陽線，
    實體收盤價明確站上 MA5 關鍵防守線，宣告單邊跌勢結束。
    【預期商業行為】：
    此時認定為「趨勢走到最後」，系統放行平空，鎖定整波主跌利潤！
    """
    df = _build_trend_df(30, start_price=0.0210, trend="DOWN", step=0.00003)
    c_prev = df.iloc[-2]
    atr_val = 0.00008
    # 構造底端強勢反彈大陽棒：收盤價大幅突破收復 MA5 且突破前高
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [c_prev["close"], c_prev["close"] + 0.00015, c_prev["close"] - 0.00001, c_prev["close"] + 0.00013]

    quote = df.iloc[-1]["close"]
    pos = {"symbol": "龙虾/USDT", "side": "SHORT", "entry_price": 0.0210, "qty": 10000.0, "leverage": 5.0, "peak_roe": 0.025}

    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote, position=pos)
    snap.atr = atr_val
    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=quote)

    assert should_exit is True, "底端大陽線明確收復 MA5 且動能反轉，必須授權平倉落袋！"
    assert reason in ("EARLY_PIVOT_VALLEY_EXIT", "SHORT_EXIT_ON_EMA_CROSS", "SHORT_EXIT_KC_MID_BREACH", "PROFIT_LOCK")


# ==============================================================================
# 業務場景 4：【開倉位置審查】大週期是空頭，短線出現誘多暴衝，一票否決開多 (嚴禁天花板接刀)
# ==============================================================================
def test_entry_timing_blocks_long_when_macro_trend_is_bearish():
    """
    [商業場景：嚴格順勢交易，空頭趨勢嚴禁逆勢摸底]
    市場主趨勢為空頭 (大週期 CK 為 SHORT / KC 中軌陡峭下行)。
    盤中 1m 即使出現暴衝大陽棒或突破上軌，
    【預期商業行為】：
    100% 觸發 BLOCKED_LONG_ON_BEARISH_TREND 予以一票否決，防止套在反彈最高點！
    """
    df = _build_trend_df(30, start_price=0.0210, trend="DOWN", step=0.00004)
    # 最後一根構造暴衝大紅 K
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [0.0195, 0.0202, 0.0194, 0.0201]

    quote = 0.0201
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote)
    # 人工注入大週期為空頭
    snap.macro_trend = "BEARISH"
    snap.kc_middle_slope = -0.00005

    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snap)
    assert should_entry is False, "大週期為空頭時，嚴禁開多！"
    assert "BEARISH" in reason or "TREND" in reason or "VETO" in reason


# ==============================================================================
# 業務場景 5：【開倉位置審查】防追高追低邊際審查：乖離超過 0.5 ATR 嚴禁追單
# ==============================================================================
def test_entry_timing_blocks_late_chase_when_price_extended_beyond_band():
    """
    [商業場景：太晚開倉防護 (防追高天花板)]
    行情已經在 KC 上軌之外狂飆了多根 K 棒，最新價距離 KC 外軌已達 0.8 ATR (超過 0.5 ATR 乖離上限)。
    此時開倉成本極差，極易被隨後的回踩打爆。
    【預期商業行為】：
    Gate 必須直接觸發 LATE_CHASE_BAND_EXTENSION 拒絕進場，寧可錯過絕不追高！
    """
    df = _build_trend_df(30, start_price=0.0200, trend="UP", step=0.00003)
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.0215)
    snap.atr = 0.0001
    snap.kc_upper = 0.0210
    # 乖離 = 0.0215 - 0.0210 = 0.0005 = 5.0 ATR (遠超過 0.5 ATR 限制)

    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snap)
    assert should_entry is False, "破軌乖離過大時，必須一票否決追單！"


# ==============================================================================
# 業務場景 6：【防未見底誤平空】龍蝦 11:49:02 盤中微小跳動，嚴禁誤平空單
# ==============================================================================
def test_exit_timing_blocks_short_exit_on_intrabar_flutter_at_1149():
    """
    [商業場景：2026-10-22 11:49:02 龍蝦實盤邊際測試]
    行情在 11:41 開空後一路順勢大跌，在 11:48 剛收黑跌破底部，
    11:49 開盤僅 2 秒，盤中價格稍微回彈 0.00002 (遠在 MA5 之下且未破前高)。
    【預期商業行為】：
    系統必須強制 VETO_HOLD 死抱到底，絕不允許被 2 秒的盤中跳動誤判為谷底反彈！
    """
    df = _build_trend_df(30, start_price=0.02050, trend="DOWN", step=0.000005)
    # 前一根探底陰線 (11:48)
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [0.02027, 0.02027, 0.02022, 0.02024]
    # 當前盤中棒 (11:49:02，開盤僅 2 秒微小跳動 0.00002)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [0.02024, 0.02026, 0.02024, 0.02026]

    quote = 0.02026
    pos = {"symbol": "龙虾/USDT", "side": "SHORT", "entry_price": 0.02035, "qty": 38000.0, "peak_roe": 0.003}
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote, position=pos)
    snap.atr = 0.00006
    snap.kc_upper = 0.02045
    snap.kc_lower = 0.02027

    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=quote)
    assert should_exit is False, f"11:49:02 未收復 MA5 且未突破前高，嚴禁平空！actual={reason}"
    assert reason == "VETO_HOLD"
    assert "MA5" in meta.get("cause", "") or "死抱" in meta.get("cause", "")


# ==============================================================================
# 業務場景 7：【MA 交叉後開倉】MA5 金叉 MA15 且站穩中軌，及時開多
# ==============================================================================
def test_entry_timing_ma_cross_and_midline_reclaim_allows_long():
    """
    [商業場景：用戶問題 1 - 可以在 MA 交叉後開倉嗎？]
    在 11:58 附近，MA5 向上金叉 MA15，K 棒收紅陽線明確站穩 KC 中軌之上，中軌走平。
    【預期商業行為】：
    系統必須放行 ENTRY_LONG_EMA_CROSS_MID 開多，及時捕捉波段發起點！
    """
    df = _build_trend_df(30, start_price=0.02030, trend="UP", step=0.000008)
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.02040)
    snap.curr_k["open"] = 0.02035
    snap.curr_k["close"] = 0.02040
    snap.curr_k["high"] = 0.02041
    snap.curr_k["low"] = 0.02034
    snap.kc_middle = 0.02036
    snap.kc_middle_slope = 0.000002
    snap.curr_k["ma5"] = 0.02038
    snap.prev_k["ma5"] = 0.02034
    snap.curr_k["ma15"] = 0.02036
    snap.prev_k["ma15"] = 0.02035

    should_entry, reason, meta = EntryGate.evaluate("ENTRY_LONG_EMA_CROSS_MID", snap)
    assert should_entry is True, f"MA5向上金叉MA15且站穩中軌，必須放行開多！actual={reason}, meta={meta}"
    assert "CROSS" in reason or "BREAKOUT" in reason or "ENTRY" in reason


# ==============================================================================
# 業務場景 8：【破軌開倉與外軌鎖定抱牢】破軌及時開倉，外軌暴走嚴禁提早秒平
# ==============================================================================
def test_entry_and_hold_timing_breakout_allows_entry_and_locks_outside_kc():
    """
    [商業場景：用戶問題 2 - 破軌也沒開倉？請修正]
    在 12:02 ~ 12:04，價格強勢衝破 KC 上軌之外。
    【預期商業行為】：
    1. EntryGate 必須放行 BREAKOUT_LONG 開多，絕不卡死在遲鈍的大週期慢線上！
    2. 開多進場後，價格仍在 KC 上軌之外狂飆，ExitGate 必須外軌鎖定死抱 (VETO_HOLD)，
       絕對嚴禁因大週期 EMA 慢線滯後而在 3 秒內惡意秒平！
    """
    df = _build_trend_df(30, start_price=0.02030, trend="UP", step=0.000008)
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.02046)
    snap.atr = 0.00006
    snap.kc_upper = 0.02042
    snap.kc_middle = 0.02036
    snap.kc_middle_slope = 0.000005
    snap.curr_k["ma5"] = 0.02044
    snap.prev_k["ma5"] = 0.02038
    snap.curr_k["open"] = 0.02040
    snap.curr_k["close"] = 0.02046
    snap.curr_k["high"] = 0.02047
    snap.curr_k["low"] = 0.02039
    snap.ema_fast = 0.02038
    snap.ema_slow = 0.02040 # 大週期慢線仍在延遲中

    # 1. 驗證破軌及時開倉放行
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snap)
    assert should_entry is True, f"破軌向上時必須放行開多！actual={reason}"

    # 2. 驗證進場後外軌鎖定抱牢 (絕不被 ema_fast < ema_slow 秒平)
    pos_long = {"symbol": "龙虾/USDT", "side": "LONG", "entry_price": 0.02045, "qty": 38000.0, "peak_roe": 0.002}
    should_exit, exit_reason, exit_meta = ExitGate.evaluate(pos_long, snap, current_price=0.02046)
    assert should_exit is False, f"價格在KC上軌外狂飆，嚴禁提早秒平！actual={exit_reason}, meta={exit_meta}"
    assert exit_reason == "VETO_HOLD"
    assert "外軌" in exit_meta.get("cause", "") or "暴走" in exit_meta.get("cause", "") or "抱牢" in exit_meta.get("cause", "")
