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
    assert any(k in exit_meta.get("cause", "") for k in ("外軌", "暴走", "抱牢", "大週期", "死抱", "健康回踩"))


# ==============================================================================
# 業務場景 9：【圖二回歸測試】主升浪中途收小黑棒回踩，守穩 MA5 嚴禁平多，必須繼續走
# ==============================================================================
def test_image2_pullback_above_ma5_strictly_holds_long():
    """
    [商業場景：用戶圖二 - 平多不應發生，應該要繼續走]
    在 12:04 買多後，行情沿著 KC 上軌延伸。
    在 12:06 出現一根小陰棒回踩，但該棒收盤價依然穩穩踩在 MA5 之上 (甚至在 KC 上軌外)，
    持倉尚未展開至高額止盈門檻。
    【預期商業行為】：
    系統必須強制判定為「空中換手/健康回踩」，觸發 VETO_HOLD 死抱到底，絕不允許被洗下車！
    """
    df = _build_trend_df(30, start_price=0.02030, trend="UP", step=0.000008)
    # 前一根衝頂陽線
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [0.02042, 0.02048, 0.02041, 0.02046]
    # 當前小陰棒回踩 (收盤價 0.02044，穩穩踩在 MA5 0.02039 之上)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [0.02046, 0.02047, 0.02043, 0.02044]

    quote = 0.02044
    pos = {"symbol": "龙虾/USDT", "side": "LONG", "entry_price": 0.02045, "qty": 38000.0, "peak_roe": 0.007}
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote, position=pos)
    snap.curr_k["ma5"] = 0.02040
    snap.prev_k["ma5"] = 0.02036
    snap.kc_upper = 0.02042

    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=quote)
    assert should_exit is False, f"圖二場景：價格守穩在 MA5 之上，嚴禁平多！actual={reason}, meta={meta}"
    assert reason == "VETO_HOLD"
    assert any(k in meta.get("cause", "") for k in ("MA5", "呼吸", "死抱", "外軌", "大週期", "健康回踩"))


# ==============================================================================
# 業務場景 10：【用戶問題 4 & 5 回歸測試】空頭大週期嚴禁開多，第二個買多拒絕追高
# ==============================================================================
def test_bearish_macro_trend_strictly_blocks_long():
    """
    [商業場景：用戶問題 5 - 如果這是空頭大週期是不是都不要開倉才對]
    在空頭大週期 (macro_trend == 'BEARISH') 下，哪怕盤中突破破軌或金叉，
    順勢交易鐵律一票否決，100% 嚴禁開多接飛刀！
    """
    df = _build_trend_df(30, start_price=0.02030, trend="UP", step=0.000008)
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.02046)
    snap.macro_trend = "BEARISH"
    snap.kc_upper = 0.02042
    snap.curr_k["open"] = 0.02040
    snap.curr_k["close"] = 0.02046
    snap.curr_k["ma5"] = 0.02044
    snap.prev_k["ma5"] = 0.02038

    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snap)
    assert should_entry is False, "空頭大週期下嚴禁開多！"
    assert reason == "BLOCKED_LONG_ON_BEARISH_TREND"
    assert "空頭" in meta.get("cause", "") or "BEARISH" in meta.get("cause", "")


def test_second_long_blocked_by_stale_continuation():
    """
    [商業場景：用戶問題 4 - 第二個買多不應再開]
    行情已在 KC 上軌外連續收線超過 2 根，或屬於延續追高，
    系統必須強制觸發 LATE_CHASE_STALE_CONTINUATION 拒絕開倉，嚴禁追在天花板！
    """
    df = _build_trend_df(30, start_price=0.02030, trend="UP", step=0.000008)
    # 人工構造過去多根收在上軌外
    for idx in range(-5, -1):
        df.loc[df.index[idx], "close"] = 0.02045
        df.loc[df.index[idx], "kc_upper"] = 0.02040

    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.02046)
    snap.kc_upper = 0.02040
    snap.atr = 0.00005
    snap.curr_k["open"] = 0.02044
    snap.curr_k["close"] = 0.02046
    snap.curr_k["high"] = 0.02046
    snap.curr_k["low"] = 0.02043
    snap.curr_k["ma5"] = 0.02045
    snap.prev_k["ma5"] = 0.02042

    should_entry, reason, meta = EntryGate.evaluate("CONTINUATION_OUTSIDE_KC_LONG", snap)
    assert should_entry is False, "延續開倉軌外超過2根或乖離過大，必須拒絕追第二個買多！"
    assert reason in ("LATE_CHASE_STALE_CONTINUATION", "LATE_CHASE_BAND_EXTENSION")


# ==============================================================================
# 業務場景 11：【最新實盤覆盤圖】中間巨型大陰線砸爛結構，反彈第二個買多一票否決
# ==============================================================================
def test_image_giant_bearish_bar_breakdown_blocks_subsequent_long():
    """
    [商業場景：最新用戶圖 - 中間長黑貫穿上下軌後，右側假反彈第二個買多被套]
    中間出現長黑實體陰線 (body >= 2.0 ATR) 破壞多頭通道結構。
    隨後反彈碰上軌，但冷卻期未滿 10 根 K 棒，
    系統必須強制一票否決 BLOCKED_RECENT_WATERFALL_BREAKDOWN，嚴禁接刀追多！
    """
    df = _build_trend_df(30, start_price=0.02030, trend="UP", step=0.000008)
    # 人工在倒數第 4 根插入一根巨型長黑柱 (body = 0.00015 >= 2.0 * atr 0.00005)
    atr_val = 0.00005
    giant_idx = df.index[-4]
    df.loc[giant_idx, "open"] = 0.02050
    df.loc[giant_idx, "close"] = 0.02035  # body = 0.00015 = 3 * ATR
    df.loc[giant_idx, "high"] = 0.02052
    df.loc[giant_idx, "low"] = 0.02034
    df.loc[giant_idx, "atr"] = atr_val

    # 最新這根假反彈至上軌
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.02046)
    snap.atr = atr_val
    snap.kc_upper = 0.02044
    snap.curr_k["open"] = 0.02040
    snap.curr_k["close"] = 0.02046
    snap.curr_k["high"] = 0.02047
    snap.curr_k["low"] = 0.02039
    snap.curr_k["ma5"] = 0.02043
    snap.prev_k["ma5"] = 0.02038

    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snap)
    assert should_entry is False, f"巨型大陰線破壞結構後，必須一票否決開多！actual={reason}"
    assert reason == "BLOCKED_RECENT_WATERFALL_BREAKDOWN"
    assert "巨型" in meta.get("cause", "") or "結構受創" in meta.get("cause", "")


def test_two_bar_breakout_no_new_high_blocks_long():
    """
    [商業場景：兩根破軌確認 - 第 2 根未突破第 1 根高點拒絕開多]
    第 1 根已破軌收在軌外 (high=0.02050, close=0.02048)。
    第 2 根雖然也在軌外，但高點僅 0.02049 (未過前高)，實體偏小且帶上影線。
    系統必須強制一票否決 BLOCKED_BREAKOUT_NO_NEW_HIGH，防止誘多！
    """
    df = _build_trend_df(30, start_price=0.02030, trend="UP", step=0.000008)
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=0.02047)
    snap.raw_df = None
    snap.kc_upper = 0.020455
    snap.atr = 0.00010
    # 第 1 根破軌已在軌外
    snap.prev_k = {
        "open": 0.02042,
        "high": 0.02050,
        "low": 0.02041,
        "close": 0.02048,
        "kc_upper": 0.020455,
        "ma5": 0.02044,
    }
    # 第 2 根未突破前高 (high 0.02049 < 0.02050, close 0.02048 <= 0.02048)
    snap.curr_k = {
        "open": 0.02044,
        "high": 0.02049,
        "low": 0.02043,
        "close": 0.02048,
        "kc_upper": 0.020455,
        "ma5": 0.02045,
    }

    should_entry, reason, meta = EntryGate.evaluate("CONFIRMED_2BAR_BREAKOUT", snap)
    assert should_entry is False, f"兩根破軌未創新高，必須一票否決！actual={reason}"
    assert reason == "BLOCKED_BREAKOUT_NO_NEW_HIGH"


# ==============================================================================
# 業務場景 12：【最新實盤覆盤圖】空單主跌浪中，弱勢綠棒摸 MA5 留長上影線嚴禁平空
# ==============================================================================
def test_image_bear_run_minor_bounce_touches_ma5_strictly_holds_short():
    """
    [商業場景：最新用戶圖 - 空單主跌浪中，兩根弱勢綠棒摸紅線 MA5 卻觸發錯誤平空]
    空單主跌浪中，兩根綠棒反彈僅摸到紅色短期均線 (MA5)，留下很長上影線，
    收盤依然受壓於 MA5 之下，完全沒有摸到上方中軌與慢線，且 MA5 斜率依然向下。
    【預期商業行為】：
    系統必須強制判定為「良性回抽/空中換手」，觸發 VETO_HOLD 死抱到底，絕不允許被微小反抽洗下車！
    """
    df = _build_trend_df(30, start_price=0.0210, trend="DOWN", step=0.00004)
    # 前一根下探陰線
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [0.02060, 0.02061, 0.02048, 0.02050]
    # 當前綠棒反彈：最高摸到 0.02056 (摸 MA5)，但收盤僅 0.02052，留長上影線且受壓於 MA5=0.02054
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [0.02050, 0.02056, 0.02049, 0.02052]

    quote = 0.02052
    pos = {"symbol": "龙虾/USDT", "side": "SHORT", "entry_price": 0.0210, "qty": 10000.0, "leverage": 5.0, "peak_roe": 0.04}
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote, position=pos)
    snap.raw_df = None
    snap.kc_upper = 0.02080
    snap.kc_lower = 0.02040
    snap.curr_k["ma5"] = 0.02054
    snap.prev_k["ma5"] = 0.02058  # MA5 依然陡峭向下
    snap.kc_middle = 0.02065       # 遠在上方中軌
    snap.kc_middle_slope = -0.00001
    snap.ema_fast = 0.02068
    snap.ema_slow = 0.02075       # 大週期均線空頭排列

    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=quote)
    assert should_exit is False, f"空頭主跌浪中摸 MA5 留長上影線，嚴禁平空！actual={reason}, meta={meta}"
    assert reason == "VETO_HOLD"
    assert any(k in meta.get("cause", "") for k in ("MA5", "死抱", "健康反抽", "呼吸", "大週期"))


# ==============================================================================
# 業務場景 13：【用戶 19:18 龍蝦暴賺回歸測試】首根大陽噴出後收小紅跌棒，MA5向上且守穩中軸嚴禁平多！
# ==============================================================================
def test_image_1918_lobster_breakout_breath_candle_holds_position():
    """
    [商業場景：用戶 19:18 龍蝦截圖 - 首根大陽噴發後小紅跌棒，不應提早平多被洗出場]
    在 18:54 龍蝦大陽棒暴衝 (open=0.02127, close=0.02177, high=0.02183)，峰值 ROE 達 18%。
    在 18:55 出現一根小跌棒微幅喘息 (open=0.02175, close=0.02171)，
    若只看階梯鎖利 (PROFIT_LOCK) 或單根小跌棒恐慌，會在這裡提早平多，踏空後面 18:57~18:58 暴衝到 0.02240+ 的主升段！
    【預期商業行為】：
    MA5 依然強烈向上抬頭 (0.021404 > 0.021286)，且收盤遠在起漲棒中軸 (0.02152) 之上，
    階梯鎖利與一般平多訊號必須 100% 被一票否決 (VETO_HOLD)，一路死抱到真正的頂部反轉！
    """
    df = _build_trend_df(30, start_price=0.02080, trend="UP", step=0.00003)
    # 18:54 大陽線衝刺
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [0.02127, 0.02183, 0.02127, 0.02177]
    # 18:55 小跌棒喘息 (中軸 0.02152，當前收盤 0.02171 遠高於中軸)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [0.02175, 0.02177, 0.02162, 0.02171]

    quote = 0.02171
    pos = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 0.02100,
        "qty": 35000.0,
        "leverage": 5.0,
        "peak_roe": 0.18,  # 峰值ROE達18%
    }
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote, position=pos)
    snap.curr_k["ma5"] = 0.021404
    snap.prev_k["ma5"] = 0.021286
    snap.kc_upper = 0.02160
    snap.kc_middle = 0.02115
    snap.kc_middle_slope = 0.00001
    snap.ema_fast = 0.02120
    snap.ema_slow = 0.02100

    should_exit, reason, meta = ExitGate.evaluate(pos, snap, current_price=quote)
    assert should_exit is False, f"MA5抬頭且守穩起漲棒中軸，嚴禁階梯鎖利或小跌棒提早平多！actual={reason}, meta={meta}"
    assert reason == "VETO_HOLD"
    assert meta.get("ma5_slope_shield") is True or "MA5" in meta.get("cause", "")


# ==============================================================================
# 業務場景 14：【用戶 19:18 龍蝦半山腰開空回歸測試】大週期多頭趨勢中嚴禁開空 (消除 0.021328 追空被套)
# ==============================================================================
def test_image_1918_lobster_blocks_short_at_021328_on_bullish_trend():
    """
    [商業場景：用戶 19:18 龍蝦截圖 - 19:14 逆大級別趨勢在 0.021328 追空被套痛點]
    龍蝦在 0.02070 打底後巨幅拉出大陽線暴衝到 0.02240，隨後頂部下殺回調至 0.021328。
    此時雖然短期 K 棒急殺跌破中軌，但大週期均線依然為多頭排列 (EMA15 > EMA40 棕色慢線，EMA60 向上)，
    且價格依然在棕色慢線之上。
    【預期商業行為】：
    1. 大週期 Trend Bias Filter 強制生效，大趨勢向上時禁用做空按鈕 (LONG_ONLY 模式)！
    2. EntryGate 收到任何空頭進場訊號 (ENTRY_SHORT_EMA_CROSS_MID 或 BREAKDOWN_SHORT)，
       100% 觸發 BLOCKED_SHORT_ON_BULLISH_TREND 予以一票否決，徹底杜絕半山腰逆勢做空！
    """
    df = _build_trend_df(40, start_price=0.02070, trend="UP", step=0.00004)
    # 頂部回踩 K 棒 (19:14 報價 0.021328)
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [0.02135, 0.02137, 0.02131, 0.021328]

    quote = 0.021328
    snap = DataPipeline.calculate_snapshot("龙虾/USDT", df, current_price=quote)
    snap.ema_fast = 0.02143
    snap.ema_slow = 0.02135  # EMA15 > EMA40 棕色慢線 (多頭排列)
    snap.slow_slope = 0.00001
    snap.macro_trend = "BULLISH"
    snap.kc_middle = 0.02143
    snap.kc_middle_slope = -0.000007 # 單根短線回踩雜訊

    # 1. 驗證交叉開空訊號被 100% 阻斷
    should_entry, reason, meta = EntryGate.evaluate("ENTRY_SHORT_EMA_CROSS_MID", snap)
    assert should_entry is False, f"大週期多頭趨勢中，絕對嚴禁開空！actual={reason}, meta={meta}"
    assert reason == "BLOCKED_SHORT_ON_BULLISH_TREND"

    # 2. 驗證破軌下殺開空訊號亦被 100% 阻斷
    should_entry_b, reason_b, meta_b = EntryGate.evaluate("BREAKDOWN_SHORT", snap)
    assert should_entry_b is False, f"大週期多頭趨勢中，破軌下殺亦嚴禁開空！actual={reason_b}, meta={meta_b}"
    assert reason_b == "BLOCKED_SHORT_ON_BULLISH_TREND"



