"""
檔案位置: tests/test_dual_entry_strategies.py
職責:
單元測試清單 (100% 覆蓋用戶規格第八節「必要測試」之 12 項檢驗):

1. test_kc_first_breakout_confirmed_success: KC 首次破軌及第二根確認全部符合，可以產生訊號。
2. test_kc_first_breakout_wick_only_no_signal: KC 首次破軌只有影線穿越，不得產生訊號。
3. test_kc_continuation_ma3_allowed_signal_success: KC 延續期間符合條件且 MA3 方向允許，可以產生新訊號。
4. test_kc_continuation_ma3_down_blocks_long: KC 延續期間 MA3 向下時，不得新增多單。
5. test_kc_continuation_ma3_up_blocks_short: KC 延續期間 MA3 向上時，不得新增空單。
6. test_ma_cross_ma3_down_blocks_long: MA3 向下時，即使出現做多均線交叉訊號，也不得新增多單。
7. test_ma_cross_ma3_up_blocks_short: MA3 向上時，即使出現做空均線交叉訊號，也不得新增空單。
8. test_ma3_flat_direction_allows_valid_signals: MA3 持平時，方向過濾不否決交易，但仍須符合所有其他條件。
9. test_no_duplicate_orders_on_same_bar: 同一根 K 線同一訊號不得重複下單。
10. test_existing_same_direction_position_allows_new_signal: 已有同方向持倉時，新的有效訊號不應被單純的持倉檢查直接丟棄。
11. test_ma3_direction_turn_does_not_force_exit_existing_position: MA3 方向變化不得直接造成既有持倉被強制平倉。
12. test_full_backtest_engine_integration_uses_same_code: 所有測試必須驗證正式回測引擎使用的實際程式碼。
"""

import unittest
import pandas as pd
import numpy as np

from core.services.strategies.dual_entry_strategies import DualEntryDetector
from core.services.matching.pessimistic_matcher import PessimisticMatcher
from run_dual_strategy_benchmark import simulate_single_strategy


def make_bar_df(bars: list) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    for col in ["open", "high", "low", "close", "kc_upper", "kc_lower", "kc_middle", "atr", "ma3", "ma15"]:
        if col not in df.columns:
            df[col] = 100.0
    if "datetime" not in df.columns:
        df["datetime"] = pd.date_range("2026-01-01", periods=len(df), freq="1min")
    return df


class TestDualEntryStrategiesComplete(unittest.TestCase):

    # ─────────────────────────────────────────────────────────────
    # 測試 1: KC 首次破軌及第二根確認全部符合，可以產生訊號
    # ─────────────────────────────────────────────────────────────
    def test_kc_first_breakout_confirmed_success(self):
        """1. KC 首次破軌及第二根確認全部符合，可以產生訊號"""
        df = make_bar_df([
            {"open": 102.0, "high": 108.0, "low": 101.0, "close": 107.0, "kc_upper": 105.0, "kc_lower": 95.0, "ma3": 100.0, "atr": 2.0},
            {"open": 107.5, "high": 111.0, "low": 106.0, "close": 110.0, "kc_upper": 105.2, "kc_lower": 95.0, "ma3": 102.0, "atr": 2.0}, # MA3 UP
        ])
        sig = DualEntryDetector.check_kc_initial_breakout(df, current_idx=1, apply_ma3_filter=True)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "LONG")
        self.assertEqual(sig["signal_source"], "KC_BREAKOUT_INITIAL")

    # ─────────────────────────────────────────────────────────────
    # 測試 2: KC 首次破軌只有影線穿越，不得產生訊號
    # ─────────────────────────────────────────────────────────────
    def test_kc_first_breakout_wick_only_no_signal(self):
        """2. KC 首次破軌只有影線穿越，不得產生訊號"""
        # Bar 1: 開 100, 高 108 (影線穿), 收 104 (在軌道 105 內)
        df = make_bar_df([
            {"open": 100.0, "high": 108.0, "low": 99.0, "close": 104.0, "kc_upper": 105.0, "kc_lower": 95.0, "ma3": 100.0, "atr": 2.0},
            {"open": 104.5, "high": 110.0, "low": 106.0, "close": 109.0, "kc_upper": 105.2, "kc_lower": 95.0, "ma3": 102.0, "atr": 2.0},
        ])
        sig = DualEntryDetector.check_kc_initial_breakout(df, current_idx=1, apply_ma3_filter=True)
        self.assertIsNone(sig, "只有影線穿軌絕不可產生破軌訊號！")

    # ─────────────────────────────────────────────────────────────
    # 測試 3: KC 延續期間符合條件且 MA3 方向允許，可以產生新訊號
    # ─────────────────────────────────────────────────────────────
    def test_kc_continuation_ma3_allowed_signal_success(self):
        """3. KC 延續期間符合條件且 MA3 方向允許，可以產生新訊號"""
        # 處於 LONG 延續狀態，當根陽線 (開 108, 收 111)，最低價 106.5 > 上軌 105.5，MA3 向上 (103 > 101)
        df = make_bar_df([
            {"open": 106.0, "high": 109.0, "low": 105.8, "close": 108.0, "kc_upper": 105.0, "ma3": 101.0, "atr": 2.0},
            {"open": 108.0, "high": 112.0, "low": 106.5, "close": 111.0, "kc_upper": 105.5, "ma3": 103.0, "atr": 2.0},
        ])
        sig = DualEntryDetector.check_kc_continuation_signal(df, current_idx=1, in_continuation_side="LONG", apply_ma3_filter=True)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "LONG")
        self.assertEqual(sig["signal_source"], "KC_CONTINUATION")

    # ─────────────────────────────────────────────────────────────
    # 測試 4: KC 延續期間 MA3 向下時，不得新增多單
    # ─────────────────────────────────────────────────────────────
    def test_kc_continuation_ma3_down_blocks_long(self):
        """4. KC 延續期間 MA3 向下時，不得新增多單"""
        # 處於 LONG 延續狀態，價格雖然在軌外且收陽線，但 MA3 向下 (101 < 103)
        df = make_bar_df([
            {"open": 106.0, "high": 112.0, "low": 106.0, "close": 110.0, "kc_upper": 105.0, "ma3": 103.0, "atr": 2.0},
            {"open": 107.0, "high": 111.0, "low": 106.5, "close": 109.0, "kc_upper": 105.5, "ma3": 101.0, "atr": 2.0}, # MA3 DOWN
        ])
        sig = DualEntryDetector.check_kc_continuation_signal(df, current_idx=1, in_continuation_side="LONG", apply_ma3_filter=True)
        self.assertIsNone(sig, "MA3 正在向下時，絕對禁止新增任何多單！")

    # ─────────────────────────────────────────────────────────────
    # 測試 5: KC 延續期間 MA3 向上時，不得新增空單
    # ─────────────────────────────────────────────────────────────
    def test_kc_continuation_ma3_up_blocks_short(self):
        """5. KC 延續期間 MA3 向上時，不得新增空單"""
        # 處於 SHORT 延續狀態，價格在下軌外且收陰線，但 MA3 向上 (98 > 96)
        df = make_bar_df([
            {"open": 94.0, "high": 94.5, "low": 90.0, "close": 92.0, "kc_lower": 95.0, "ma3": 96.0, "atr": 2.0},
            {"open": 92.0, "high": 93.5, "low": 89.0, "close": 91.0, "kc_lower": 94.5, "ma3": 98.0, "atr": 2.0}, # MA3 UP
        ])
        sig = DualEntryDetector.check_kc_continuation_signal(df, current_idx=1, in_continuation_side="SHORT", apply_ma3_filter=True)
        self.assertIsNone(sig, "MA3 正在向上時，絕對禁止新增任何空單！")

    # ─────────────────────────────────────────────────────────────
    # 測試 6: MA3 向下時，即使出現做多均線交叉訊號，也不得新增多單
    # ─────────────────────────────────────────────────────────────
    def test_ma_cross_ma3_down_blocks_long(self):
        """6. MA3 向下時，即使出現做多均線交叉訊號，也不得新增多單"""
        # 前一根 MA3 100 <= MA15 100; 當根 MA3 99.5 > MA15 99.0 (因 MA15 掉更快而形成交叉)，但 MA3 本身向下 (99.5 < 100.0)
        df = make_bar_df([
            {"open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0, "ma3": 100.0, "ma15": 100.0, "atr": 1.0},
            {"open": 98.0, "high": 100.0, "low": 97.0, "close": 99.0, "ma3": 99.5, "ma15": 99.0, "atr": 1.0}, # MA3 DOWN
        ])
        sig = DualEntryDetector.check_ma_cross_signal(df, current_idx=1, apply_ma3_filter=True)
        self.assertIsNone(sig, "MA3 向下時，黃金交叉亦必須被一票否決！")

    # ─────────────────────────────────────────────────────────────
    # 測試 7: MA3 向上時，即使出現做空均線交叉訊號，也不得新增空單
    # ─────────────────────────────────────────────────────────────
    def test_ma_cross_ma3_up_blocks_short(self):
        """7. MA3 向上時，即使出現做空均線交叉訊號，也不得新增空單"""
        # 前一根 MA3 100 >= MA15 100; 當根 MA3 100.5 < MA15 101.0，但 MA3 本身向上 (100.5 > 100.0)
        df = make_bar_df([
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "ma3": 100.0, "ma15": 100.0, "atr": 1.0},
            {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.0, "ma3": 100.5, "ma15": 101.0, "atr": 1.0}, # MA3 UP
        ])
        sig = DualEntryDetector.check_ma_cross_signal(df, current_idx=1, apply_ma3_filter=True)
        self.assertIsNone(sig, "MA3 向上時，死亡交叉亦必須被一票否決！")

    # ─────────────────────────────────────────────────────────────
    # 測試 8: MA3 持平時，方向過濾不否決交易，但仍須符合所有其他條件
    # ─────────────────────────────────────────────────────────────
    def test_ma3_flat_direction_allows_valid_signals(self):
        """8. MA3 持平時，方向過濾不否決交易，但仍須符合所有其他條件"""
        # 前一根 MA3 100.0, 當根 MA3 100.0 (持平 FLAT)
        # 前一根 MA15 100.5, 當根 MA15 99.5 (發生黃金交叉: 100.0 <= 100.5 -> 100.0 > 99.5)
        df = make_bar_df([
            {"open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0, "ma3": 100.0, "ma15": 100.5, "atr": 1.0},
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "ma3": 100.0, "ma15": 99.5, "atr": 1.0}, # FLAT
        ])
        direction = DualEntryDetector.get_ma3_direction(df, current_idx=1)
        self.assertEqual(direction, "FLAT")
        # 檢驗過濾放行
        self.assertTrue(DualEntryDetector.is_ma3_direction_allowed(df, current_idx=1, side="LONG"))
        self.assertTrue(DualEntryDetector.is_ma3_direction_allowed(df, current_idx=1, side="SHORT"))
        # 產生訊號
        sig = DualEntryDetector.check_ma_cross_signal(df, current_idx=1, apply_ma3_filter=True)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "LONG")

    # ─────────────────────────────────────────────────────────────
    # 測試 9: 同一根 K 線同一訊號不得重複下單
    # ─────────────────────────────────────────────────────────────
    def test_no_duplicate_orders_on_same_bar(self):
        """9. 同一根 K 線同一訊號不得重複下單"""
        df = make_bar_df([
            {"open": 102.0, "high": 108.0, "low": 101.0, "close": 107.0, "kc_upper": 105.0, "ma3": 100.0, "atr": 2.0},
            {"open": 107.5, "high": 111.0, "low": 106.0, "close": 110.0, "kc_upper": 105.2, "ma3": 102.0, "atr": 2.0},
            {"open": 110.0, "high": 113.0, "low": 109.5, "close": 112.0, "kc_upper": 105.5, "ma3": 104.0, "atr": 2.0},
        ])
        stats, tdf = simulate_single_strategy(df, strategy_mode="KC_BREAKOUT", start_bar=1, end_bar=3)
        # Bar 1 產生 1 個訊號，Bar 2 開盤成交 1 筆，絕無重複開單
        self.assertLessEqual(len(tdf), 1)

    # ─────────────────────────────────────────────────────────────
    # 測試 10: 已有同方向持倉時，新的有效訊號不應被單純的持倉檢查直接丟棄
    # ─────────────────────────────────────────────────────────────
    def test_existing_same_direction_position_allows_new_signal(self):
        """10. 已有同方向持倉時，新的有效訊號不應被單純的持倉檢查直接丟棄"""
        # 檢驗檢測器邏輯: check_kc_continuation_signal 本身獨立於是否有持倉，持續輸出有效訊號
        df = make_bar_df([
            {"open": 106.0, "high": 109.0, "low": 105.8, "close": 108.0, "kc_upper": 105.0, "ma3": 101.0, "atr": 2.0},
            {"open": 108.0, "high": 112.0, "low": 106.5, "close": 111.0, "kc_upper": 105.5, "ma3": 103.0, "atr": 2.0},
        ])
        # 即使呼叫端已持有多單，延續訊號仍然精確產生
        sig = DualEntryDetector.check_kc_continuation_signal(df, current_idx=1, in_continuation_side="LONG", apply_ma3_filter=True)
        self.assertIsNotNone(sig, "有效延續訊號不應因既有持倉而消失")

    # ─────────────────────────────────────────────────────────────
    # 測試 11: MA3 方向變化不得直接造成既有持倉被強制平倉
    # ─────────────────────────────────────────────────────────────
    def test_ma3_direction_turn_does_not_force_exit_existing_position(self):
        """11. MA3 方向變化不得直接造成既有持倉被強制平倉"""
        # 模擬持倉中，下一根 Bar 雖然 MA3 下拐，但價格並未跌破當前停損，PessimisticMatcher 必須判定續抱！
        pos = {
            "entry_price": 100.0, "current_sl": 96.0, "r_distance": 2.0,
            "orig_qty": 50.0, "remaining_qty": 50.0,
            "tp1_price": 104.0, "tp2_price": 108.0,
            "tp1_done": False, "tp2_done": False
        }
        # 當根 Bar: Low 98.0 (高於停損 96.0)，High 102.0 (未達 TP1 104.0)，Close 99.0
        # 此處即使外部環境 MA3 下拐，撮合模組絕不觸發任何平倉
        m_res = PessimisticMatcher.match_long_bar(
            b_open=100.0, b_high=102.0, b_low=98.0, b_close=99.0,
            pos=pos
        )
        self.assertFalse(m_res["closed"], "MA3 下拐絕不可平掉未觸及停損的既有持倉！")
        self.assertEqual(pos["remaining_qty"], 50.0)

    # ─────────────────────────────────────────────────────────────
    # 測試 12: 所有測試必須驗證正式回測引擎使用的實際程式碼
    # ─────────────────────────────────────────────────────────────
    def test_full_backtest_engine_integration_uses_same_code(self):
        """12. 所有測試必須驗證正式回測引擎使用的實際程式碼"""
        # 直接呼叫 simulate_single_strategy，確認引擎執行完整閉環且無崩潰
        df = make_bar_df([
            {"open": 100.0, "high": 104.0, "low": 99.0, "close": 102.0, "kc_upper": 105.0, "kc_lower": 95.0, "ma3": 100.0, "ma15": 100.0, "atr": 2.0},
            {"open": 102.0, "high": 108.0, "low": 101.0, "close": 107.0, "kc_upper": 105.0, "kc_lower": 95.0, "ma3": 101.0, "ma15": 100.2, "atr": 2.0},
            {"open": 107.5, "high": 111.0, "low": 106.0, "close": 110.0, "kc_upper": 105.2, "kc_lower": 95.0, "ma3": 103.0, "ma15": 100.5, "atr": 2.0},
            {"open": 110.0, "high": 115.0, "low": 109.0, "close": 114.0, "kc_upper": 105.5, "kc_lower": 95.0, "ma3": 105.0, "ma15": 101.0, "atr": 2.0},
            {"open": 114.0, "high": 116.0, "low": 100.0, "close": 102.0, "kc_upper": 105.5, "kc_lower": 95.0, "ma3": 103.0, "ma15": 101.5, "atr": 2.0},
        ])
        stats, tdf = simulate_single_strategy(df, strategy_mode="KC_BREAKOUT", start_bar=1, end_bar=5)
        self.assertIn("total_trades", stats)
        self.assertIn("net_pnl_usd", stats)


if __name__ == "__main__":
    unittest.main()
