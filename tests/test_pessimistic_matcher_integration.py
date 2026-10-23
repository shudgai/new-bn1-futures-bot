"""
檔案位置: tests/test_pessimistic_matcher_integration.py
整合測試: 驗證正式 SSOT 撮合模組 (core.services.matching.pessimistic_matcher)
專門補齊用戶審查指出的缺口:
1. 跳空穿越停損價 (Slippage beyond SL on Gap)
2. 倉位數量總和守恆 (30% + 30% + 40% = 100%)
3. 多 Bar 連續推演中的狀態機精確轉移
4. 成本不重複計算與帳務守恆
"""

import unittest
from core.services.matching.pessimistic_matcher import PessimisticMatcher


class TestPessimisticMatcherIntegration(unittest.TestCase):

    def test_gap_down_beyond_sl_long(self):
        """多單跳空低開穿越停損: 進場 100, 停損 98, 開盤 95, 低點 94 -> 必須以最差開盤價 95 成交"""
        pos = {
            "entry_price": 100.0, "orig_qty": 50.0, "remaining_qty": 50.0,
            "current_sl": 98.0, "r_distance": 2.0, "tp1_price": 104.0, "tp2_price": 108.0,
            "tp1_done": False, "tp2_done": False
        }
        res = PessimisticMatcher.match_long_bar(
            b_open=95.0, b_high=96.0, b_low=94.0, b_close=95.5, pos=pos,
            taker_fee_rate=0.0005, slippage_rate=0.0005
        )
        self.assertTrue(res["closed"])
        self.assertEqual(res["exit_price"], 95.0)  # 以跳空開盤價最悲觀成交
        self.assertEqual(res["realized_gross_pnl"], (95.0 - 100.0) * 50.0)
        self.assertEqual(pos["remaining_qty"], 0.0)

    def test_gap_up_beyond_sl_short(self):
        """空單跳空高開穿越停損: 進場 100, 停損 102, 開盤 105, 高點 106 -> 必須以最差開盤價 105 成交"""
        pos = {
            "entry_price": 100.0, "orig_qty": 50.0, "remaining_qty": 50.0,
            "current_sl": 102.0, "r_distance": 2.0, "tp1_price": 96.0, "tp2_price": 92.0,
            "tp1_done": False, "tp2_done": False
        }
        res = PessimisticMatcher.match_short_bar(
            b_open=105.0, b_high=106.0, b_low=104.0, b_close=104.5, pos=pos,
            taker_fee_rate=0.0005, slippage_rate=0.0005
        )
        self.assertTrue(res["closed"])
        self.assertEqual(res["exit_price"], 105.0)  # 以跳空開盤價最悲觀成交
        self.assertEqual(res["realized_gross_pnl"], (100.0 - 105.0) * 50.0)
        self.assertEqual(pos["remaining_qty"], 0.0)

    def test_multi_bar_quantity_conservation_and_fees(self):
        """
        三根連續 Bar 推演，驗證數量守恆與成本守恆:
        - 初始倉位: 100 股
        - Bar 1: 達成 TP1 (+2R) -> 平 30 股, 剩 70 股, 停損移至 100
        - Bar 2: 達成 TP2 (+4R) -> 平 30 股, 剩 40 股, 停損移至 102 (+1R)
        - Bar 3: 回踩觸發 +1R 停損 -> 平 40 股, 剩 0 股, 部位完全結束
        驗證: 平倉總數量 30 + 30 + 40 == 100，無任何股數遺漏或浮動部位殘留！
        """
        pos = {
            "entry_price": 100.0, "orig_qty": 100.0, "remaining_qty": 100.0,
            "current_sl": 98.0, "r_distance": 2.0, "tp1_price": 104.0, "tp2_price": 108.0,
            "tp1_done": False, "tp2_done": False
        }

        # Bar 1: 順利達成 TP1
        res1 = PessimisticMatcher.match_long_bar(101.0, 105.0, 100.5, 104.5, pos)
        self.assertFalse(res1["closed"])
        self.assertEqual(pos["remaining_qty"], 70.0)
        self.assertEqual(pos["current_sl"], 100.0)

        # Bar 2: 順利達成 TP2, 且低點守在 103 (> 102)
        res2 = PessimisticMatcher.match_long_bar(105.0, 109.0, 103.0, 108.5, pos)
        self.assertFalse(res2["closed"])
        self.assertEqual(pos["remaining_qty"], 40.0)
        self.assertEqual(pos["current_sl"], 102.0)

        # Bar 3: 回踩擊穿 102 (Low=101.5)
        res3 = PessimisticMatcher.match_long_bar(108.0, 108.5, 101.5, 101.8, pos)
        self.assertTrue(res3["closed"])
        self.assertEqual(res3["exit_price"], 102.0)
        self.assertEqual(pos["remaining_qty"], 0.0)  # 倉位歸零

        # 累計各批次平倉總額
        total_pnl = res1["realized_net_pnl"] + res2["realized_net_pnl"] + res3["realized_net_pnl"]
        # TP1毛利: (104-100)*30 = +120
        # TP2毛利: (108-100)*30 = +240
        # 尾倉毛利: (102-100)*40 = +80
        total_gross = res1["realized_gross_pnl"] + res2["realized_gross_pnl"] + res3["realized_gross_pnl"]
        self.assertAlmostEqual(total_gross, 120.0 + 240.0 + 80.0, places=4)
        self.assertLess(total_pnl, total_gross)  # 確保手續費與滑價完整扣除


if __name__ == "__main__":
    unittest.main()
