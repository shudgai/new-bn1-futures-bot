"""
檔案位置: tests/test_pessimistic_matching_state_machine.py
單元測試: 驗證極限悲觀撮合狀態機 (Pessimistic Order Matching State Machine)
覆蓋情境:
1. 同根 K 線同時觸及原始停損與 TP1 -> 停損最優先, 絕不計 TP1
2. 同根 K 線觸及 TP1 但同時跌破進場價 (保本價) -> 判定保本出場, 作廢 TP1
3. 同根 K 線先達成 TP1, 再達 TP2, 但 Low 跌破新停損 (+1R) -> 尾倉 40% 當根在 +1R 結算, 絕不裸露持倉
4. 空單對稱極端情境檢驗
"""

import unittest
import numpy as np


from core.services.matching.pessimistic_matcher import PessimisticMatcher


class TestPessimisticMatching(unittest.TestCase):

    def setUp(self):
        # 基準多單: 進場 100, 初始停損 98 (R=2), TP1=104 (+2R), TP2=108 (+4R), 數量=100
        self.pos = {
            "side": "LONG",
            "entry_price": 100.0,
            "r_distance": 2.0,
            "orig_qty": 100.0,
            "remaining_qty": 100.0,
            "current_sl": 98.0,
            "tp1_price": 104.0,
            "tp2_price": 108.0,
            "tp1_done": False,
            "tp2_done": False,
        }

    def test_sl_priority_over_tp1_same_bar(self):
        """同根 K 線 High 達 105 (過 TP1) 但 Low 跌到 97 (破原始停損 98) -> 必須停損出局"""
        res = PessimisticMatcher.match_long_bar(
            b_open=99.0, b_high=105.0, b_low=97.0, b_close=101.0,
            pos=self.pos
        )
        self.assertTrue(res["closed"])
        self.assertEqual(res["exit_reason"], "初始結構停損觸發")
        self.assertEqual(res["pos"]["remaining_qty"], 0.0)
        self.assertFalse(res["pos"]["tp1_done"])  # 絕不可算 TP1 獲利

    def test_tp1_and_breakeven_same_bar(self):
        """同根 K 線未打到 98, High 達 105 (過 TP1) 但 Low 跌到 99 (低於進場 100) -> 判定保本平倉, 作廢 TP1"""
        res = PessimisticMatcher.match_long_bar(
            b_open=100.0, b_high=105.0, b_low=99.0, b_close=103.0,
            pos=self.pos
        )
        self.assertTrue(res["closed"])
        self.assertEqual(res["exit_reason"], "TP1與保本同根觸碰_悲觀保本平倉")
        self.assertEqual(res["pos"]["remaining_qty"], 0.0)
        # 損益應為扣除手續費與滑價的微小負數
        self.assertLess(res["realized_pnl_delta"], 0.0)

    def test_clean_tp1_hold(self):
        """同根 K 線 High 達 105, 但 Low 守穩 101 (>100) -> 正常達成 TP1, 剩餘 70% 續抱, 停損移至 100"""
        res = PessimisticMatcher.match_long_bar(
            b_open=101.0, b_high=105.0, b_low=101.0, b_close=104.5,
            pos=self.pos
        )
        self.assertFalse(res["closed"])
        self.assertTrue(res["pos"]["tp1_done"])
        self.assertEqual(res["pos"]["remaining_qty"], 70.0)
        self.assertEqual(res["pos"]["current_sl"], 100.0)
        self.assertGreater(res["realized_pnl_delta"], 0.0)

    def test_tp2_and_runner_sl_same_bar(self):
        """同根 K 線 High 達 109 (達 TP2), 但 Low 探至 101.5 (低於 +1R 停損價 102) -> 40% 尾倉當根於 102 平倉, 不殘留裸露持倉"""
        # 假設前一根已完成 TP1, 剩餘 70%, 停損在 100
        self.pos["tp1_done"] = True
        self.pos["remaining_qty"] = 70.0
        self.pos["current_sl"] = 100.0

        res = PessimisticMatcher.match_long_bar(
            b_open=103.0, b_high=109.0, b_low=101.5, b_close=106.0,
            pos=self.pos
        )
        self.assertTrue(res["closed"])
        self.assertEqual(res["exit_reason"], "TP2後同根回踩+1R移動停利")
        self.assertEqual(res["pos"]["remaining_qty"], 0.0)
        self.assertTrue(res["pos"]["tp2_done"])

    def test_short_sl_priority_over_tp1_same_bar(self):
        """空單: 進場 100, 原始停損 102 (R=2), TP1=96, TP2=92
        同根 K 線 Low 達 95 (過 TP1) 但 High 飆到 103 (破原始停損 102) -> 必須停損出局"""
        pos_short = {
            "side": "SHORT",
            "entry_price": 100.0,
            "r_distance": 2.0,
            "orig_qty": 100.0,
            "remaining_qty": 100.0,
            "current_sl": 102.0,
            "tp1_price": 96.0,
            "tp2_price": 92.0,
            "tp1_done": False,
            "tp2_done": False,
        }
        res = PessimisticMatcher.match_short_bar(
            b_open=101.0, b_high=103.0, b_low=95.0, b_close=99.0,
            pos=pos_short
        )
        self.assertTrue(res["closed"])
        self.assertEqual(res["exit_reason"], "初始結構停損觸發")
        self.assertEqual(res["pos"]["remaining_qty"], 0.0)
        self.assertFalse(res["pos"]["tp1_done"])

    def test_short_tp1_and_breakeven_same_bar(self):
        """空單: 未碰 102, Low 達 95 (過 TP1) 但 High 升到 101 (>100) -> 判定保本平倉, 作廢 TP1"""
        pos_short = {
            "side": "SHORT",
            "entry_price": 100.0,
            "r_distance": 2.0,
            "orig_qty": 100.0,
            "remaining_qty": 100.0,
            "current_sl": 102.0,
            "tp1_price": 96.0,
            "tp2_price": 92.0,
            "tp1_done": False,
            "tp2_done": False,
        }
        res = PessimisticMatcher.match_short_bar(
            b_open=100.0, b_high=101.0, b_low=95.0, b_close=97.0,
            pos=pos_short
        )
        self.assertTrue(res["closed"])
        self.assertEqual(res["exit_reason"], "TP1與保本同根觸碰_悲觀保本平倉")
        self.assertEqual(res["pos"]["remaining_qty"], 0.0)
        self.assertLess(res["realized_pnl_delta"], 0.0)


if __name__ == "__main__":
    unittest.main()
