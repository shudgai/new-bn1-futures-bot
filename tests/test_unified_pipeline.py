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


@pytest.fixture(autouse=True)
def isolated_account_state(monkeypatch, tmp_path):
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr(
        "core.services.order_audit_trail.AUDIT_LOG_FILE",
        str(tmp_path / "audit.jsonl"),
    )


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
            "is_closed": True,
        })
    frame = pd.DataFrame(data)
    frame.attrs.update(
        timeframe_ms=60_000,
        snapshot_ms=int(frame.iloc[-1]["timestamp"]) + 60_000,
    )
    return frame


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
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=146.0)
    # 人工拉大乖離
    snapshot.ema_slow = 100.0
    snapshot.atr = 5.0
    snapshot.curr_k["open"] = 145.0
    snapshot.curr_k["high"] = 146.2
    snapshot.curr_k["low"] = 144.8
    snapshot.curr_k["close"] = 146.0  # diff > 3.0*ATR (超過上軌+3.0ATR)
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
    snapshot.kc_middle = 120.0
    snapshot.kc_lower = 116.0
    snapshot.atr = 3.0
    # 前一根回踩至 119.5 (在 EMA15~40 之間)，當前棒收紅站上 EMA15 (121.0 > 120.0)
    snapshot.prev_k = {"open": 121.0, "high": 121.5, "low": 119.5, "close": 120.0}
    snapshot.curr_k = {"open": 120.0, "high": 121.2, "low": 119.8, "close": 121.0}
    should_entry, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", snapshot)
    assert should_entry
    assert reason == "PULLBACK_RECLAIM_CONTINUATION"


def test_exit_gate_hold_through_consolidation():
    """盤整回踩不可取代已收線 KC 中軌反向的普通出場授權。"""
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
    assert meta["exit_policy"] == "PROFIT_CONFIRMED_TRUE_PIVOT_ONLY_V1"


def test_exit_gate_rejects_legacy_gross_roe_lock_without_net_lock_state():
    """舊 gross ROE 峰值不可取代目前的淨 ROE 鎖利狀態。"""
    df = _generate_sample_df(30, trend="UP")
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=106.0)
    # Keep the quote within the channel so this exercises legacy profit
    # state, not the independent opposite-rail emergency.
    snapshot.kc_lower = 95.0
    snapshot.kc_upper = 130.0
    # entry=100, 最高達 112 (+12%), 當前跌到 106 (+6%), 跌破 70% 保證線 (12% * 0.7 = 8.4%)
    pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.12, "leverage": 1.0}
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=106.0)
    assert not should_exit
    assert reason == "VETO_HOLD"
    assert meta["exit_policy"] == "PROFIT_CONFIRMED_TRUE_PIVOT_ONLY_V1"


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
    [整合測試：數學候選仍須通過市場狀態及帳戶防線]
    驗證三層流水線鏈路：
    1. 當前 K 線滿足「長黑吞噬反轉」或「破中軌」時，數學裁決閘門放行。
    2. 盤整市場不得只憑數學反轉候選越過中央市場濾網開倉。
    """
    from core.paper_account import PaperAccount
    from core.services.math_execution_gate import validate_entry_math
    import time
    now_ms = time.time() * 1000.0
    n = 45
    data = []
    for i in range(n):
        if i < 28:
            c = 101.0 - i * (1.1 / 27)
            o = c + 0.01
        elif i == 28:
            o, c = 99.86, 99.9
        else:
            step = 0.5 / (n - 29)
            o = 99.9 - (i - 29) * step
            c = 99.9 - (i - 28) * step
        data.append({
            "timestamp": int(now_ms - (n - 1 - i) * 60_000),
            "open": o,
            "high": max(o, c) + (0.02 if i > 28 else 0.2),
            "low": min(o, c) - (0.02 if i > 28 else 0.2),
            "close": c,
            "volume": 150.0 if i == n - 1 else 100.0,
            "is_closed": True,
        })
    df = pd.DataFrame(data)

    # 1. 透過 DataPipeline 計算純數值快照與完整指標列
    snapshot = DataPipeline.calculate_snapshot("LOBSTER/USDT", df, current_price=99.4)
    assert snapshot is not None
    full_df = snapshot.raw_df

    # 2. 驗證數學閘門提早開空放行
    allowed, reason = validate_entry_math(full_df, side="SHORT")
    assert allowed is True
    assert reason in (
        "ENTRY_SHORT_ENGULFING_REVERSAL",
        "ENTRY_SHORT_MID_BAND_BREAK",
        "ENTRY_MATH_APPROVED",
    )

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
    assert opened is False
    assert "LOBSTER/USDT" not in account.positions
    assert any("CENTRAL_ENTRY_GATE_VETO" in item["text"] for item in account.logs)


@pytest.mark.anyio
async def test_exit_gate_holds_plain_kc_reversal_without_true_pivot():
    """單純 KC 中軌反向不平倉，不自動反手。"""
    from core.paper_account import PaperAccount
    import time
    now_ms = int(time.time() // 60) * 60_000 - 60_000
    account = PaperAccount()
    account.positions["CAP/USDT"] = {
        "side": "SHORT",
        "entry_price": 105.0,
        "qty": 1.0,
        "peak_roe": 0.08,
        "margin": 21.0,
        "leverage": 5,
        "open_timestamp": (now_ms - 30 * 60_000) / 1000,
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
            "is_closed": True,
        })
    df = pd.DataFrame(bars)
    df.attrs.update(timeframe_ms=60_000, snapshot_ms=now_ms + 60_000)

    # KC middle is rising against the SHORT; keep price within the outer
    # bands so the ordinary confirmed-reversal authority is tested alone.
    df.loc[n - 1, ["open", "high", "low", "close"]] = [98.1, 98.3, 98.0, 98.2]

    res = await ExecutionManager.process_symbol_pipeline(
        symbol="CAP/USDT",
        engine_or_account=account,
        klines_df=df,
        quote=98.2,
    )
    assert res["status"] == "HOLD"
    assert res["reason"] == "HOLD_UNTIL_PROFIT_CONFIRMED_TRUE_PIVOT"
    assert "CAP/USDT" in account.positions
    assert not account.trades


@pytest.mark.anyio
async def test_replay_rejects_flat_ma15_midline_cross_without_room():
    """長時間黏合後單根反彈，仍須通過現行 MA5／KC 位置防線。"""
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
            "open": 0.06645,
            "high": 0.06665,
            "low": 0.06625,
            "close": 0.06645,
            "volume": 100.0,
            "is_closed": True,
        })

    # 第 51~52 根紅 K 連續推升，穿過中軌並引發 EMA15 金叉 EMA40
    # 第 51 根
    bars.append({"timestamp": now_ms - 120_000, "open": 0.06645, "high": 0.06647, "low": 0.06642, "close": 0.06644, "volume": 150.0, "is_closed": True})
    # 第 52 根：金叉穿過 KC 中軌 (Close=0.06684 > Mid=0.06675, Upper=0.06695)
    bars.append({"timestamp": now_ms - 60_000, "open": 0.06676, "high": 0.06686, "low": 0.06674, "close": 0.06684, "volume": 250.0, "is_closed": True})

    df = pd.DataFrame(bars)

    res = await ExecutionManager.process_symbol_pipeline(
        symbol=symbol,
        engine_or_account=account,
        klines_df=df,
        quote=0.06684,
    )

    # MA5 尚黏合中軌，單根反彈不能無條件開倉。
    assert res["status"] == "VETO"
    assert res["reason"] == "BLOCKED_MA5_MIDLINE_ADHERED"
    assert symbol not in account.positions
    assert account.opened == []


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
    snapshot_long["macro_trend"] = "BEARISH"
    snapshot_long["raw_df"] = pd.DataFrame([
        {**snapshot_long["prev_k"], "timestamp": 60_000, "kc_middle": 114.95, "is_closed": True},
        {**snapshot_long["curr_k"], "timestamp": 120_000, "kc_middle": 115.0, "is_closed": True},
    ])
    snapshot_long["raw_df"].attrs["timeframe_ms"] = 60_000
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
    snapshot_short["macro_trend"] = "BULLISH"
    snapshot_short["raw_df"] = pd.DataFrame([
        {**snapshot_short["prev_k"], "timestamp": 60_000, "kc_middle": 105.05, "is_closed": True},
        {**snapshot_short["curr_k"], "timestamp": 120_000, "kc_middle": 105.0, "is_closed": True},
    ])
    snapshot_short["raw_df"].attrs["timeframe_ms"] = 60_000
    # 此時 KC_Pos = (99 - 100) / (110 - 100) = -0.10 < 0.20
    # 外軌乖離 = 100 - 99 = 1.0 <= 0.5 * 3.0 = 1.5 ATR
    should_entry_s, reason_s, meta_s = EntryGate.evaluate("CONFIRMED_2BAR_BREAKOUT", snapshot_short)
    assert should_entry_s is True, f"兩根破軌開空被誤殺！reason: {reason_s}, meta: {meta_s}"
    assert reason_s in ("CONFIRMED_2BAR_BREAKOUT", "BREAKOUT_MAIN_WAVE_START")
    assert meta_s.get("side") == "SHORT"


def test_profit_lock_cannot_override_hold_without_confirmed_kc_reversal():
    """舊階梯鎖利即使已啟動，亦不可取代已收線 KC 反向出口。"""
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "highest_price": 104.0,
        "qty": 1.0,
        "leverage": 5.0,
    }
    # 衝高至 104.0 後回踩至 102.5，KC 中軌為 101.5 且向上 (slope=0.05)，ATR=2.0
    # 回撤 = 104.0 - 102.5 = 1.5 < 1.5 * 2.0 = 3.0 ATR
    snapshot = {
        "current_price": 102.5,
        "atr": 2.0,
        "kc_upper": 105.0,
        "kc_middle": 101.5,
        "kc_lower": 98.0,
        "kc_middle_slope": 0.05,
        "ema_fast": 102.8,
        "ema_slow": 101.5,
    }
    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=102.5)
    assert should_exit is False
    assert reason == "VETO_HOLD"
    assert meta["suppressed_exit_reason"] == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert meta["exit_policy"] == "PROFIT_CONFIRMED_TRUE_PIVOT_ONLY_V1"


@pytest.mark.anyio
@pytest.mark.parametrize("symbol", ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("exit_reason,distance", [
    ("CIRCUIT_BREAKER_HARD_SL", 3.0),
    ("OPPOSITE_KC_BAND_BREACH", 1.1),
    ("EXIT_BY_EXTREME_WATERFALL", 1.6),
])
@pytest.mark.parametrize("new_policy", [False, True])
async def test_pipeline_preserves_safety_authority_through_real_close(
    monkeypatch, symbol, side, exit_reason, distance, new_policy,
):
    from core.paper_account import PaperAccount
    from test_kc_wave_exit import position, wave_frame

    frame = wave_frame(side, reversed_direction=False, live=True)
    pos = position(side, frame)
    pos["symbol"] = symbol
    if new_policy:
        from core.services.exits.hard_stop_service import NEW_POSITION_LOSS_POLICY

        pos["position_loss_policy"] = NEW_POSITION_LOSS_POLICY
    price = 100.0 - distance if side == "LONG" else 100.0 + distance
    upper, lower = (101.0, 99.0) if exit_reason == "OPPOSITE_KC_BAND_BREACH" else (110.0, 90.0)
    frame["kc_upper"], frame["kc_lower"], frame["atr"] = upper, lower, 1.0
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "kc_middle"]] = [
        100.0, max(100.0, price) + 0.1, min(100.0, price) - 0.1, price, 100.0,
    ]
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=price, position=pos)
    assert snapshot is not None
    snapshot.raw_df = frame
    snapshot.curr_k = frame.iloc[-1].to_dict()
    snapshot.prev_k = frame.iloc[-2].to_dict()
    snapshot.kc_upper, snapshot.kc_middle, snapshot.kc_lower = upper, 100.0, lower
    snapshot.atr = 1.0
    snapshot.macro_trend = "NEUTRAL"
    should_exit, reason, _ = ExitGate.evaluate(pos, snapshot)
    expected = exit_reason == "OPPOSITE_KC_BAND_BREACH"
    assert should_exit is expected
    if expected:
        assert reason == exit_reason
    monkeypatch.setattr(
        DataPipeline, "calculate_snapshot", staticmethod(
            lambda *args, **kwargs: snapshot,
        ),
    )
    account = PaperAccount()
    account.positions[symbol] = pos
    result = await ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=frame, quote=price,
    )
    assert result["status"] == ("EXIT_EXECUTED" if expected else "HOLD"), result
    assert (symbol not in account.positions) is expected
    assert [trade["action"] for trade in account.trades] == (["CLOSE_" + side] if expected else [])


@pytest.mark.anyio
@pytest.mark.parametrize("symbol", ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_pipeline_safety_label_cannot_bypass_final_close_evidence(monkeypatch, symbol, side):
    from core.paper_account import PaperAccount
    from test_kc_wave_exit import position, wave_frame

    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    pos["symbol"] = symbol
    account = PaperAccount()
    account.positions[symbol] = pos
    monkeypatch.setattr(
        ExitGate, "evaluate", staticmethod(
            lambda *args, **kwargs: (True, "OPPOSITE_KC_BAND_BREACH", {}),
        ),
    )
    result = await ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=frame, quote=100.2,
    )
    assert result["status"] == "HOLD"
    assert result["reason"] == "EXIT_BLOCKED_HOLD:OPPOSITE_KC_BAND_BREACH"
    assert account.positions[symbol] is pos
    assert not account.trades
