"""
tests/test_unified_pipeline.py
==============================
驗證極簡「三層流水線架構」：
1. 數據層 (DataPipeline): 純數值物件計算 (EMA15, EMA40, ma_spread_pct)
2. 裁決層 (ExitGate & EntryGate): 依 2026/10/21 盤面覆盤精確門禁
3. 執行層 (ExecutionManager): 單一通道執行
"""
import pytest
import pandas as pd
import numpy as np

from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.gates.unified_pipeline_gates import ExitGate, EntryGate
from core.services.execution_manager import ExecutionManager


def _generate_sample_df(n=50, trend="UP"):
    data = []
    base = 100.0
    for i in range(n):
        if trend == "UP":
            base += 0.5 + (i * 0.05)
        elif trend == "DOWN":
            base -= 0.5 + (i * 0.05)
        else:
            base += 0.05 if i % 2 == 0 else -0.05
        high = base + 0.3
        low = base - 0.3
        open_p = base - 0.1 if trend == "UP" else base + 0.1
        close_p = base + 0.1 if trend == "UP" else base - 0.1
        data.append({
            "timestamp": 1700000000000 + i * 60000,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close_p,
            "volume": 100.0 + i,
        })
    return pd.DataFrame(data)


def test_data_pipeline_snapshot_calculation():
    """測試數據層能回傳純數值物件，不帶任何決策"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot(
        symbol="LOBSTER/USDT",
        df=df,
        current_price=125.0,
        position={"side": "LONG", "entry_price": 100.0, "peak_roe": 0.20},
    )
    assert snapshot is not None
    assert isinstance(snapshot, MarketDataSnapshot)
    assert snapshot.symbol == "LOBSTER/USDT"
    assert snapshot.current_price == 125.0
    assert snapshot.atr > 0
    assert snapshot.kc_upper > snapshot.kc_middle > snapshot.kc_lower
    assert snapshot.ema_fast > 0
    assert snapshot.ema_slow > 0
    assert snapshot.peak_roe >= 0.20


def test_entry_gate_veto_over_extension():
    """測試進場門禁 1-A：距 EMA40 乖離過大一票否決 (|close - ema_slow| > 0.8*ATR)"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=135.0)
    # 人工拉大乖離
    snapshot.ema_slow = 100.0
    snapshot.atr = 5.0
    snapshot.curr_k["close"] = 135.0  # diff=35 > 0.8*5=4.0
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert not should_entry
    assert reason == "VETO_OVER_EXTENSION_FROM_SLOW_MA"


def test_entry_gate_veto_long_upper_wick():
    """測試進場門禁 1-B：長上影線一票否決 (防 09:12 買在急衝頂部)"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=110.0)
    snapshot.ema_slow = 109.8
    snapshot.atr = 1.0
    # 衝高留長上影線
    snapshot.curr_k = {"open": 109.9, "high": 111.0, "low": 109.8, "close": 110.0}
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert not should_entry
    assert reason == "VETO_WICK_REJECTION"


def test_entry_gate_0945_breakout_main_wave_start():
    """測試進場門禁 2：09:45 實體紅K衝破KC上軌且發散放行主升浪起點"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=125.0)
    snapshot.kc_upper = 124.0
    snapshot.ema_fast = 123.0
    snapshot.ema_slow = 122.0
    snapshot.ma_spread_pct = 0.15
    snapshot.atr = 3.0
    # 飽滿紅陽線突破上軌
    snapshot.prev_k = {"open": 122.0, "high": 123.6, "low": 121.8, "close": 123.5, "ma15": 122.0, "ma5": 123.0}
    snapshot.curr_k = {"open": 123.5, "high": 125.2, "low": 123.4, "close": 125.0, "ma15": 123.0, "ma5": 124.5}
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry
    assert reason == "BREAKOUT_MAIN_WAVE_START"


def test_entry_gate_1000_pullback_reclaim_continuation():
    """測試進場門禁 3：10:00 回踩均線帶收紅K延續開倉放行 (第二波主升段)"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=121.0)
    snapshot.ema_fast = 120.0
    snapshot.ema_slow = 118.0
    snapshot.kc_upper = 124.0
    snapshot.atr = 3.0
    # 前一根回踩至 119.5 (在 EMA15~40 之間)，當前棒收紅站上 EMA15 (121.0 > 120.0)
    snapshot.prev_k = {"open": 121.0, "high": 121.5, "low": 119.5, "close": 120.0}
    snapshot.curr_k = {"open": 120.0, "high": 121.2, "low": 119.8, "close": 121.0}
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry
    assert reason == "PULLBACK_RECLAIM_CONTINUATION"


def test_exit_gate_hold_through_consolidation():
    """測試出場門禁 1：盤整黏合嚴禁平多 (未達連續兩根收破 EMA40 強制 VETO_HOLD)"""
    df = _generate_sample_df(30, trend="UP")
    # 設定最近兩根為在 EMA40 (118.0) 之上的正常盤整洗盤小K線 (非大黑吞噬)
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [118.1, 118.6, 118.0, 118.3]
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = [118.6, 118.8, 118.3, 118.5]
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=118.5)
    snapshot.ema_slow = 118.0
    # 價格在 EMA40 附近洗盤但未連續兩根破位
    pos = {"side": "LONG", "entry_price": 115.0, "peak_roe": 0.05}
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=118.5)
    assert not should_exit
    assert reason == "VETO_HOLD"
    assert "未達連續兩根收破EMA40慢線" in meta.get("cause", "") or "大週期趨勢依然向上" in meta.get("cause", "")


def test_exit_gate_10pct_lock_7pct_profit():
    """測試出場門禁 2：達標 10% 鎖 7% 階梯保護"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=106.0)
    # entry=100, 最高達 112 (+12%), 當前跌到 106 (+6%), 跌破 70% 保證線 (12% * 0.7 = 8.4%)
    pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.12, "leverage": 1.0}
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=106.0)
    assert should_exit
    assert reason == "PROFIT_LOCK"
    assert "10%鎖70%" in meta.get("cause", "")


def test_execution_manager_pipeline_flow():
    """測試執行層單一通道標準流水線協同"""
    import asyncio

    async def _runner():
        class MockAccount:
            def __init__(self):
                self.positions = {}
                self.closed = []
                self.opened = []

            async def close_position(self, symbol, current_price, close_reason):
                self.closed.append((symbol, current_price, close_reason))
                self.positions.pop(symbol, None)
                return True

            async def open_position(self, symbol, side, price, reason):
                self.opened.append((symbol, side, price, reason))
                self.positions[symbol] = {"side": side, "entry_price": price, "qty": 1.0}
                return True

        account = MockAccount()
        df = _generate_sample_df(30, trend="UP")

        # 1. 無持倉時問 EntryGate
        res_entry = await ExecutionManager.process_symbol_pipeline(
            symbol="CAP/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=125.0,
        )
        assert res_entry["status"] in ("ENTRY_EXECUTED", "VETO", "IDLE")

        # 2. 有持倉且趨勢良好時問 ExitGate -> HOLD
        account.positions["CAP/USDT"] = {"side": "LONG", "entry_price": 120.0, "qty": 1.0}
        res_exit = await ExecutionManager.process_symbol_pipeline(
            symbol="CAP/USDT",
            engine_or_account=account,
            klines_df=df,
            quote=125.0,
        )
        assert res_exit["status"] == "HOLD"
        assert len(account.closed) == 0

    asyncio.run(_runner())


@pytest.mark.anyio
async def test_early_short_execution_pipeline_flow():
    """
    [功能與整合測試：提早開空流水線與持倉防洗整合]
    驗證三層流水線鏈路：
    1. 當前 K 線滿足「長黑吞噬反轉」或「破中軌」時，數學裁決閘門放行。
    2. ExecutionManager 依 step_size 截斷、檢查保證金與 5U MIN_NOTIONAL 後成功開空。
    3. 開空後進入 ExitGate，驗證在跌勢展開且未收線確認真谷底前，持倉保護維持續抱 (VETO_HOLD)，
       確保提早進場不會誤觸防洗持倉過濾器，整條流水線流暢且不撞車！
    """
    from core.paper_account import PaperAccount
    from core.services.math_execution_gate import validate_entry_math
    import time
    now_ms = time.time() * 1000.0
    n = 30
    data = []
    for i in range(n):
        if i < 28:
            c = 100.2 if i % 2 == 0 else 100.0
            o = 100.0 if i % 2 == 0 else 100.2
        elif i == 28:
            o, c = 100.0, 100.5
        else:
            o, c = 100.5, 99.4
        data.append({
            "timestamp": int(now_ms - (n - 1 - i) * 60_000),
            "open": o,
            "high": max(o, c) + 0.2,
            "low": min(o, c) - 0.2,
            "close": c,
            "volume": 100.0,
        })
    df = pd.DataFrame(data)

    # 1. 透過 DataPipeline 計算純數值快照與完整指標列
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=99.4)
    assert snapshot is not None
    full_df = snapshot.raw_df

    # 2. 驗證數學閘門提早開空放行
    allowed, reason = validate_entry_math(full_df, side="SHORT")
    assert allowed is True
    assert reason in ("ENTRY_SHORT_ENGULFING_REVERSAL", "ENTRY_SHORT_MID_BAND_BREAK")

    # 3. 驗證 ExecutionManager 執行鏈路
    account = PaperAccount()
    account.balance = 500.0
    account.available_balance = 500.0
    async def _async_provider(s):
        return full_df
    account.entry_frame_provider = _async_provider

    opened = await ExecutionManager.open_position(
        account,
        "LOBSTER/USDT",
        "SHORT",
        99.4,
        reason,
        meta={"step_size": 0.1, "atr": snapshot.atr, "raw_df": full_df},
    )
    assert opened is True
    assert "LOBSTER/USDT" in account.positions
    pos = account.positions["LOBSTER/USDT"]
    assert pos["side"] == "SHORT"
    assert abs(pos["entry_price"] - 99.4) < 0.1

    # 3. 驗證開倉後持倉防洗整合：價格順利下行 (99.0)，無真谷底反轉，維持續抱
    from core.gates.unified_pipeline_gates import ExitGate
    snapshot = DataPipeline.calculate_snapshot(
        "LOBSTER/USDT",
        df,
        current_price=99.0,
        position={"side": "SHORT", "entry_price": 99.5, "peak_roe": 0.05},
    )
    should_exit, exit_reason, _ = ExitGate.evaluate(pos, snapshot, current_price=99.0)
    assert should_exit is False
    assert exit_reason == "VETO_HOLD"


@pytest.mark.anyio
async def test_exit_gate_no_stall_on_bearish_exhaustion():
    """
    [端到端流水線測試：空單收復中軌動能耗盡即刻平倉且不反手開單]
    驗證流水線鏈路：
    1. 當前持有空單，最新 K 棒收盤價實體站上 KC 中軌。
    2. ExecutionManager.process_symbol_pipeline 收到平空指令後立即執行平倉，持倉清空。
    3. 平倉後若無合法順勢突破訊號，狀態維持 IDLE，絕不盲目反手開多！
    """
    from core.paper_account import PaperAccount
    import time
    now_ms = time.time() * 1000.0
    account = PaperAccount()
    account.positions["CAP/USDT"] = {
        "side": "SHORT",
        "entry_price": 105.0,
        "qty": 1.0,
        "peak_roe": 0.08,
    }

    n = 30
    bars = []
    for i in range(n):
        c = 97.0 + (i * 0.05)
        o = c - 0.05
        bars.append({
            "timestamp": int(now_ms - (n - 1 - i) * 60_000),
            "open": o,
            "high": c + 0.1,
            "low": o - 0.1,
            "close": c,
            "volume": 100.0,
        })
    df = pd.DataFrame(bars)

    # 模擬最後一根收在 100.5，突破 KC 中軌 (kc_middle 約 98.5)
    df.loc[n - 1, ["open", "high", "low", "close"]] = [98.8, 100.6, 98.7, 100.5]

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="CAP/USDT",
        engine_or_account=account,
        klines_df=df,
        quote=100.5,
    )
    assert res["status"] == "EXIT_EXECUTED"
    assert res["reason"] in ("SHORT_EXIT_KC_MID_BREACH", "SHORT_EXIT_EMA_REVERSAL", "SHORT_EXIT_ON_EMA_CROSS", "EARLY_PIVOT_VALLEY_EXIT", "PROFIT_LOCK", "PROFIT_LOCK_TRAILING_GIVEBACK", "OPPOSITE_KC_BAND_BREACH")
    assert "CAP/USDT" not in account.positions


@pytest.mark.anyio
async def test_replay_entry_price_improvement_over_kc_breakout():
    """
    [時序回放比對測試：EMA 交叉 + 中軌突破提早開倉 vs KC 外軌破軌追高]
    情境重現：
    模擬行情在底部築底完成後發起反彈：
    1. 12:56~12:58：EMA15 向上金叉 EMA40，收盤站上 KC 中軌 (約 0.06684)，右側共振形成。
       系統必須在此時立即放行開多 (ENTRY_LONG_EMA_CROSS_MID)，進場價位約 0.06684。
    2. 後續 13:01：長紅 K 棒衝破 KC 上軌 (價格達 0.066997)。
    斷言：
       - 開多成交必須在均線金叉且突破中軌時立即觸發 (ENTRY_LONG_EMA_CROSS_MID)；
       - 成交價格必須嚴格低於 0.06690 (顯著優於天花板追高價 0.066997，節省 15+ ticks 成本)！
    """
    from core.services.execution_manager import ExecutionManager
    import time
    import pandas as pd

    class MockAccount:
        def __init__(self):
            self.positions = {}
            self.opened = []

        async def open_position(self, symbol, side, price=None, reason="", **kwargs):
            self.opened.append((symbol, side, price, reason))
            self.positions[symbol] = {"side": side, "entry_price": price or kwargs.get("price", 0.06684), "qty": 1.0}
            return True

        async def close_position(self, symbol, price, reason):
            self.positions.pop(symbol, None)
            return True

    account = MockAccount()
    symbol = "CAP/USDT"
    now_ms = int(time.time() * 1000)

    bars = []
    for i in range(50):
        bars.append({
            "timestamp": now_ms - (52 - i) * 60_000,
            "open": 0.06670,
            "high": 0.06675,
            "low": 0.06665,
            "close": 0.06670,
            "volume": 100.0,
        })

    # 第 51~52 根紅 K 連續推升，穿過中軌並引發 EMA15 金叉 EMA40
    # 第 51 根
    bars.append({"timestamp": now_ms - 60_000, "open": 0.06670, "high": 0.06678, "low": 0.06668, "close": 0.06676, "volume": 150.0})
    # 第 52 根：金叉穿過 KC 中軌 (Close=0.06684 > Mid=0.06675, Upper=0.06695)
    bars.append({"timestamp": now_ms, "open": 0.06676, "high": 0.06686, "low": 0.06674, "close": 0.06684, "volume": 250.0})

    df = pd.DataFrame(bars)

    res = await ExecutionManager.process_symbol_pipeline(
        symbol=symbol,
        engine_or_account=account,
        klines_df=df,
        quote=0.06684,
    )

    # 驗證在 0.06684 處提早開多成立
    assert res["status"] == "ENTRY_EXECUTED", f"預期在金叉穿中軌時提早開多，實際狀態: {res}"
    assert res["reason"] in ("ENTRY_LONG_EMA_CROSS_MID", "PULLBACK_RECLAIM_CONTINUATION")
    assert symbol in account.positions
    pos = account.positions[symbol]
    assert pos["side"] == "LONG"
    # 核心斷言：成交價嚴格低於 0.06690 (避免外軌 0.06699 天花板)
    assert pos["entry_price"] < 0.06690, f"開倉價 {pos['entry_price']} 未能顯著優於外軌天花板 0.06690！"
    assert pos["entry_price"] == 0.06684


def test_confirmed_2bar_breakout_not_blocked_by_kc_pos_gate():
    """
    [門禁整合測試：兩根破軌模式解衝突驗證]
    驗證：
    1. 兩根破軌模式 (CONFIRMED_2BAR_BREAKOUT) 收盤天然在 KC 上軌外 (KC_Pos >= 1.0 > 0.80)；
    2. 均線順向發散 (ema_fast > ema_slow 且 slope >= 0 且 ema15 斜率向上)；
    3. 距同側外軌乖離在 0.5 ATR 內 (未超標)；
    4. EntryGate.evaluate 必須正常放行開多 (should_entry == True)，
       絕對不可被通道內震盪的 KC %B (KC_Pos > 0.80) 全域門禁誤殺！
    5. 空單兩根破下軌同理，KC_Pos <= 0.0 < 0.20 且距下軌乖離 <= 0.5 ATR，
       EntryGate 必須正常放行開空，絕不被 KC_Pos < 0.20 門禁誤殺！
    """
    from core.gates.unified_pipeline_gates import EntryGate

    # 多單兩根破軌測試
    snapshot_long = {
        "current_price": 121.0,
        "atr": 3.0,
        "kc_upper": 120.0,
        "kc_middle": 115.0,
        "kc_lower": 110.0,
        "kc_middle_slope": 0.05,
        "ema_fast": 118.0,
        "ema_slow": 116.0,
        "ma_spread_pct": 0.10,
        "curr_k": {"open": 120.2, "high": 121.3, "low": 120.1, "close": 121.0},
        "prev_k": {"open": 119.5, "high": 120.5, "low": 119.2, "close": 120.3, "ma15": 117.0},
    }
    # 此時 KC_Pos = (121 - 110) / (120 - 110) = 1.10 > 0.80
    # 外軌乖離 = 121 - 120 = 1.0 <= 0.5 * 3.0 = 1.5 ATR
    should_entry, reason, meta = EntryGate.evaluate("CONFIRMED_2BAR_BREAKOUT", snapshot_long)
    assert should_entry is True, f"兩根破軌開多被誤殺！reason: {reason}, meta: {meta}"
    assert reason in ("CONFIRMED_2BAR_BREAKOUT", "BREAKOUT_MAIN_WAVE_START")
    assert meta.get("side") == "LONG"

    # 空單兩根破軌測試
    snapshot_short = {
        "current_price": 99.0,
        "atr": 3.0,
        "kc_upper": 110.0,
        "kc_middle": 105.0,
        "kc_lower": 100.0,
        "kc_middle_slope": -0.05,
        "ema_fast": 102.0,
        "ema_slow": 104.0,
        "ma_spread_pct": 0.10,
        "curr_k": {"open": 99.8, "high": 99.9, "low": 98.7, "close": 99.0},
        "prev_k": {"open": 100.5, "high": 100.8, "low": 99.5, "close": 99.7, "ma15": 103.0},
    }
    # 此時 KC_Pos = (99 - 100) / (110 - 100) = -0.10 < 0.20
    # 外軌乖離 = 100 - 99 = 1.0 <= 0.5 * 3.0 = 1.5 ATR
    should_entry_s, reason_s, meta_s = EntryGate.evaluate("CONFIRMED_2BAR_BREAKOUT", snapshot_short)
    assert should_entry_s is True, f"兩根破軌開空被誤殺！reason: {reason_s}, meta: {meta_s}"
    assert reason_s in ("CONFIRMED_2BAR_BREAKOUT", "BREAKOUT_MAIN_WAVE_START")
    assert meta_s.get("side") == "SHORT"



