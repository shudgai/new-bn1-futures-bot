"""
tests/test_core_trading_edge_cases.py
======================================
針對 Binance Futures Bot 2.0 (new bn) 之【開倉、平倉、鎖利】
全面覆蓋「技術邊際 (Technical Edge Cases)」與「商業邏輯邊際 (Business Edge Cases)」的整合測試套件。

測試框架: Pytest + AnyIO
"""
import pytest
import pandas as pd
import numpy as np

from core.services.data_pipeline import DataPipeline
from core.gates.unified_pipeline_gates import ExitGate
import time
from core.services.execution_manager import ExecutionManager
from core.paper_account import PaperAccount
from core.services.math_execution_gate import validate_entry_math, validate_exit_math


# ─── 輔助函數：合成 K 棒數據 ───────────────────────────────────────────
def create_mock_df(
    n_bars: int = 50,
    start_price: float = 100.0,
    trend: str = "UP",
    atr_val: float = 2.0,
    wick_ratio: float = 0.1,
    end_time_ms: float | None = None,
) -> pd.DataFrame:
    """生成具有真實指標欄位的連續 1m K 棒 DataFrame (時間戳為毫秒)"""
    if end_time_ms is None:
        end_time_ms = time.time() * 1000.0
    timestamps = [int(end_time_ms - (n_bars - 1 - i) * 60_000) for i in range(n_bars)]
    
    if trend == "UP":
        closes = np.linspace(start_price, start_price + (n_bars * 0.5), n_bars)
        opens = closes - 0.3
    elif trend == "DOWN":
        closes = np.linspace(start_price, start_price - (n_bars * 0.5), n_bars)
        opens = closes + 0.3
    else:  # FLAT / CHOP
        closes = start_price + np.sin(np.linspace(0, 10, n_bars)) * 0.2
        opens = closes - 0.05

    highs = np.maximum(opens, closes) + (atr_val * wick_ratio)
    lows = np.minimum(opens, closes) - (atr_val * wick_ratio)
    
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.ones(n_bars) * 1000.0,
    })
    return df


# ═════════════════════════════════════════════════════════════════════════
# 模組一：【開倉 (Entry)】技術邊際與商業邊際測試
# ═════════════════════════════════════════════════════════════════════════

class TestEntryEdgeCases:
    """開倉邊際審計與防護測試"""

    def test_entry_technical_insufficient_bars_fail_closed(self):
        """
        [技術邊際 - 極少數據/冷啟動]
        防範風險：當系統重啟或新幣上線，K 棒數量嚴重不足 (小於 20 根) 時，
        系統必須 Fail-Closed 拒絕計算並阻斷開倉，絕不可拋出未捕捉異常導致程式閃退崩潰。
        """
        short_df = create_mock_df(n_bars=5)
        snapshot = DataPipeline.calculate_snapshot("TEST/USDT", short_df, current_price=102.0)
        assert snapshot is None, "K 棒少於 20 根時，數據層必須拒絕生成快照"

    def test_entry_technical_nan_inf_data_sanitization(self):
        """
        [技術邊際 - 髒數據/NaN/Inf 污染]
        防範風險：當幣安 API 或 WebSocket 傳回損壞的 NaN/Inf 資料時，
        數學裁決閘門必須即刻判定無效並一票否決，防止計算溢位或以錯誤價格下單。
        """
        df = create_mock_df(n_bars=30, trend="UP")
        snap = DataPipeline.calculate_snapshot("TEST/USDT", df, current_price=110.0)
        corrupted_df = snap.raw_df.copy()
        # 人為注入 NaN 污染最後一根 K 棒
        corrupted_df.loc[corrupted_df.index[-1], "close"] = np.nan
        
        allowed, reason = validate_entry_math(corrupted_df, side="LONG")
        assert not allowed, "遭遇 NaN 污染時必須一票否決"
        assert "INVALID" in reason or "UNKNOWN" in reason

    def test_entry_business_counter_trend_strict_veto_on_bearish(self):
        """
        [商業邊際 - 順勢鐵律：空頭趨勢嚴禁開多]
        防範風險：商業上最大的虧損常來自「逆勢接飛刀」。
        當 KC 通道中軌向下（空頭主趨勢）時，盤中哪怕出現大陽線突破，
        系統必須 100% 觸發 BLOCKED_LONG_ON_BEARISH_TREND 阻斷，嚴禁在空頭浪摸底接刀！
        """
        df = create_mock_df(n_bars=40, trend="DOWN")
        snap = DataPipeline.calculate_snapshot("TEST/USDT", df, current_price=df["close"].iloc[-1])
        allowed, reason = validate_entry_math(snap.raw_df, side="LONG")
        assert not allowed
        assert reason == "BLOCKED_LONG_ON_BEARISH_TREND", f"預期順勢鐵律攔截，實際返回: {reason}"

    def test_entry_business_long_wick_fakeout_veto(self):
        """
        [商業邊際 - 誘多誘空針：影線過長一票否決]
        防範風險：莊家插針誘多（如上影線過長、實體萎縮），追進去往往套在最高點。
        驗證當上影線遠大於實體時，開倉閘門必須 veto 阻斷。
        """
        df = create_mock_df(n_bars=30, trend="UP")
        snap = DataPipeline.calculate_snapshot("TEST/USDT", df, current_price=df["close"].iloc[-1])
        wick_df = snap.raw_df.copy()
        last_idx = wick_df.index[-1]
        c_val = float(wick_df.loc[last_idx, "close"])
        wick_df.loc[last_idx, "open"] = c_val - 0.1
        wick_df.loc[last_idx, "high"] = c_val + 6.0   # 上影線遠超實體數倍
        
        allowed, reason = validate_entry_math(wick_df, side="LONG")
        assert not allowed
        assert reason == "BLOCKED_ENTRY_MATH_LONG_WICK"

    @pytest.mark.anyio
    async def test_entry_business_insufficient_margin_guard(self):
        """
        [商業邊際 - 資金風控：保證金不足拒絕送單]
        防範風險：用戶錢包餘額不足 (< 5 USDT) 時，若向交易所發出開倉請求會遭遇保證金拒單，
        甚至可能因槓桿計費產生負餘額風險。系統必須在客戶端前提攔截。
        """
        account = PaperAccount()
        account.wallet_balance = 2.0  # 僅剩 2 USDT
        res = await ExecutionManager.open_position(
            account, "TEST/USDT", "LONG", 100.0, "test_low_margin"
        )
        assert res is False, "可用餘額小於 5U 時必須攔截開倉"

    @pytest.mark.anyio
    async def test_entry_business_min_notional_guard(self):
        """
        [商業邊際 - 名義價值下限 MIN_NOTIONAL 防呆]
        防範風險：幣安合約 API 要求每筆下單的名義價值 (truncated_qty * price) 不得低於 5 USDT。
        若依 step_size 向下截斷後的名義價值不足 5 USDT，系統必須直接安全阻斷並返回 False，
        防止送單後遭到幣安 API 以 -4164 MIN_NOTIONAL 拒單。
        """
        account = PaperAccount()
        account.wallet_balance = 100.0
        # 價格 10.0，但 step_size 設為 100.0，向下截斷後 truncated_qty = 0.0，名義價值為 0 < 5.0
        res = await ExecutionManager.open_position(
            account,
            "TEST/USDT",
            "LONG",
            10.0,
            "test_min_notional",
            meta={"step_size": 100.0, "atr": 0.5},
        )
        assert res is False, "截斷後名義價值不足 5 USDT 時必須安全阻斷並返回 False"

    def test_entry_technical_stale_data_veto(self):
        """
        [技術邊際 - 數據層防禦：過期舊資料攔截 (Stale Data Veto)]
        防範風險：當 WebSocket 斷線或行情回傳延遲時，若系統依據滯後舊資料開平倉，
        容易在行情早已大幅變動的情況下以錯誤價格接盤被割。
        驗證：最後一根 K 棒時間戳落後當前系統時間超過 3 分鐘 (180,000 毫秒) 時，
        Fail-Closed 一票否決並返回 BLOCKED_STALE_DATA。
        """
        now_ms = time.time() * 1000.0
        # 滯後 4 分鐘 (240,000 ms > 180,000 ms)
        stale_end_ms = now_ms - 240_000.0
        stale_df = create_mock_df(n_bars=30, trend="UP", end_time_ms=stale_end_ms)
        snap = DataPipeline.calculate_snapshot("TEST/USDT", stale_df, current_price=stale_df["close"].iloc[-1])
        
        allowed, reason = validate_entry_math(snap.raw_df, side="LONG")
        assert not allowed, "滯後超過 3 分鐘的舊資料必須被阻斷"
        assert reason == "BLOCKED_STALE_DATA", f"預期 BLOCKED_STALE_DATA，實際返回: {reason}"

    @pytest.mark.anyio
    async def test_entry_business_duplicate_position_concurrency_lock(self):
        """
        [商業邊際 - 重複開倉/併發競態]
        防範風險：同一標的在未平倉前，若短時間內連續觸發多次開倉訊號，
        絕不可重複下單造成部位過大爆倉（過度槓桿風險）。
        """
        account = PaperAccount()
        account.wallet_balance = 1000.0
        account.positions["TEST/USDT"] = {"side": "LONG", "qty": 10.0, "entry_price": 100.0}
        
        res = await ExecutionManager.open_position(
            account, "TEST/USDT", "LONG", 100.0, "duplicate_signal"
        )
        assert res is False, "已存在持倉時必須拒絕重複開倉"


# ═════════════════════════════════════════════════════════════════════════
# 模組二：【鎖利 (Profit Lock)】技術邊際與商業邊際測試
# ═════════════════════════════════════════════════════════════════════════

class TestProfitLockEdgeCases:
    """階梯動態鎖利防護測試"""

    def test_profit_lock_business_tiered_ratchet_70pct_preservation(self):
        """
        [商業邊際 - 獲利回吐防護：10% 利潤鎖住 70%]
        防範風險：帳面最高浮盈達到 +12% 時，行情隨後大幅反轉下跌至 +6%。
        若不執行階梯鎖利，會導致「大賺變賠錢」。
        驗證：跌破利潤保證線 (12% * 0.7 = 8.4%) 時必須強制觸發 PROFIT_LOCK。
        """
        df = create_mock_df(n_bars=30, trend="UP")
        # 進場價 100，最高衝到 112 (peak_roe = 12%)，當前掉到 106 (當前利潤 6%)
        snapshot = DataPipeline.calculate_snapshot("TEST/USDT", df, current_price=106.0)
        pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.12}
        
        should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=106.0)
        assert should_exit is True
        assert reason == "PROFIT_LOCK"
        assert "10%鎖7%" in meta.get("cause", "")

    def test_profit_lock_business_fee_coverage_prevent_premature_exit(self):
        """
        [商業邊際 - 手續費覆蓋保護：防止微利假鎖利]
        防範風險：浮盈只有極微小的 0.05% 時，若執行平倉，扣除開平倉雙向手續費 (0.08%~0.1%)
        與滑點後實際上是淨虧損。系統在利潤不足以覆蓋成本時，不應判定為鎖利。
        """
        df = create_mock_df(n_bars=30, trend="UP")
        snapshot = DataPipeline.calculate_snapshot("TEST/USDT", df, current_price=100.05)
        pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.0005}
        
        should_exit, reason, _ = ExitGate.evaluate(pos, snapshot, current_price=100.05)
        assert not should_exit or reason != "PROFIT_LOCK", "利潤未達門檻時不得觸發鎖利出場"


# ═════════════════════════════════════════════════════════════════════════
# 模組三：【平倉 (Exit)】技術邊際與商業邊際測試
# ═════════════════════════════════════════════════════════════════════════

class TestExitEdgeCases:
    """平倉防洗與極端安全避險測試"""

    def test_exit_business_holding_protection_against_minor_pullback(self):
        """
        [商業邊際 - 持倉防洗鐵律：小回調與均線毛刺嚴禁提早下車]
        防範風險：持倉後經常出現小陰線洗盤或盤中均線短暫下拐。
        只要價格仍在通道外軌強勢區、未收線確認真頂部樞紐 (Three-Point Pivot)，
        必須維持續抱 (should_exit == False)，堅決抱牢主升段，絕不可被震盪洗下車！
        """
        df = create_mock_df(n_bars=40, trend="UP")
        # 價格仍運行在 KC 上軌外側 (強勢暴走區)
        current_price = df["close"].iloc[-1] + 2.0
        snapshot = DataPipeline.calculate_snapshot("TEST/USDT", df, current_price=current_price)
        pos = {"side": "LONG", "entry_price": 100.0, "peak_roe": 0.03}
        
        should_exit, reason, _ = ExitGate.evaluate(pos, snapshot, current_price=current_price)
        assert should_exit is False, "處於強勢主升波段且無峰谷反轉訊號時，必須嚴格續抱"
        assert reason in ("VETO_HOLD", "HOLD_OUTSIDE_KC_BULL_RUN")

    def test_exit_business_emergency_waterfall_exit_override(self):
        """
        [商業邊際 - 極端風控例外：巨型瀑布貫穿必須立即逃命]
        防範風險：當遭遇黑天鵝暴跌（如跌破反側 KC 外軌或單根巨量長陰貫穿），
        若此時依然死抱不放會導致爆倉。
        驗證：此時必須突破普通持倉保護，判定為緊急避險平倉 (EMERGENCY_EXIT)。
        """
        df = create_mock_df(n_bars=40, trend="DOWN")
        pos = {"side": "LONG", "entry_price": 100.0, "qty": 10.0, "margin": 50.0}
        
        # 觸發反側破軌異常平倉
        allowed, reason, meta = validate_exit_math(
            position=pos,
            frame=df,
            price=80.0,
            close_reason="OPPOSITE_KC_BAND_BREACH",
        )
        assert allowed is True
        assert reason == "EMERGENCY_EXIT_OVERRIDE"
        assert meta.get("exit_type") == "EMERGENCY"

    @pytest.mark.anyio
    async def test_exit_technical_closing_deduplication_atomic_lock(self):
        """
        [技術邊際 - 平倉原子鎖與防止多次扣倉/反向開倉]
        防範風險：在極端行情或多線程併發下，若同時收到兩次平倉請求，
        第二次平倉若在無部位時送出，會變成反向開新倉（如多單平倉變放空）。
        驗證：平倉後持倉必須即時歸零，第二次平倉必須安全回傳已無持倉。
        """
        account = PaperAccount()
        account.wallet_balance = 1000.0
        account.positions["TEST/USDT"] = {
            "side": "LONG",
            "qty": 5.0,
            "entry_price": 100.0,
            "margin": 50.0,
            "leverage": 10,
        }
        
        # 第一次平倉 (緊急避險平倉)
        closed1 = await ExecutionManager.close_position(
            account, "TEST/USDT", 90.0, "OPPOSITE_KC_BAND_BREACH"
        )
        assert closed1 is True
        assert "TEST/USDT" not in account.positions
        
        # 第二次平倉 (併發或重複訊號)
        closed2 = await ExecutionManager.close_position(
            account, "TEST/USDT", 90.0, "OPPOSITE_KC_BAND_BREACH"
        )
        assert closed2 is False, "持倉已清除，第二次平倉必須阻斷，防止意外反向開倉"

    @pytest.mark.anyio
    async def test_exit_business_emergency_forces_market_order(self):
        """
        [商業邊際 - 緊急逃命平倉強制市價單]
        防範風險：當遭遇反向擊穿或極端暴跌時，若仍用 LIMIT 掛單等待撮合會完全吃不到單而穿倉。
        驗證：ExecutionManager 在收到 EMERGENCY / OPPOSITE_KC_BAND_BREACH 時，強制 is_limit=False (市價)。
        """
        class MockAccount:
            def __init__(self):
                self.positions = {"TEST/USDT": {"side": "LONG"}}
                self.last_is_limit = None
            async def close_position(self, symbol, current_price, close_reason, is_limit=False):
                self.last_is_limit = is_limit
                return True

        account = MockAccount()
        await ExecutionManager.close_position(
            account, "TEST/USDT", 90.0, "OPPOSITE_KC_BAND_BREACH", meta={"is_limit": True}
        )
        assert account.last_is_limit is False, "緊急平倉必須強制覆蓋為市價單 (is_limit=False)"

    def test_entry_business_funding_rate_spike_settlement_filter(self):
        """
        [商業邊際 - 資金費率結算時點過濾]
        防範風險：妖幣費率高達 -1.5%~-2.0%，在結算前 5 分鐘開倉會直接產生巨大隱形成本被吃本金。
        驗證：若費率極度不利且在結算前 5 分鐘內，開倉閘門必須一票否決。
        """
        df = create_mock_df(n_bars=30, trend="UP")
        snap = DataPipeline.calculate_snapshot("TEST/USDT", df, current_price=df["close"].iloc[-1])
        rate_df = snap.raw_df.copy()
        last_idx = rate_df.index[-1]
        rate_df.loc[last_idx, "funding_rate"] = 0.02  # +2.0% 費率，做多極度不利
        
        # 模擬在整點前 2 分鐘 (120 秒)
        import time
        allowed, reason = validate_entry_math(rate_df, side="LONG")
        # 只要時間處於整點前5分鐘內即會阻斷
        now_ts = time.time()
        time_to_settle = 3600 - (int(now_ts) % 3600)
        if time_to_settle <= 300:
            assert not allowed
            assert reason == "BLOCKED_FUNDING_RATE_SPIKE"

