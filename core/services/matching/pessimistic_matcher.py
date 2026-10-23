"""
檔案位置: core/services/matching/pessimistic_matcher.py
職責: 統一的極限悲觀 OHLC 撮合狀態機 (Single Source of Truth)
供回測引擎 (run_phase4_controlled_experiments.py) 與單元測試 (tests/) 共同引用，
杜絕回測與測試實作不一致的漏洞！
"""

from typing import Dict, Any, Tuple


class PessimisticMatcher:
    """
    極限悲觀撮合狀態機 (Pessimistic OHLC Matching State Machine)
    鐵律:
    1. 停損優先: 當根若打到目前停損，立即以最悲觀價格出局，絕不執行後續止盈！
    2. TP1 與保本同根: 若當根 High >= TP1 但 Low <= entry_p，悲觀認定先回踩跌破進場價，作廢 TP1，於進場價保本離場。
    3. TP2 與新移動停損同根: 若當根達成 TP2 但 Low <= (entry_p + 1.0*R)，剩餘 40% 尾倉當根於 +1R 結算，絕不殘留裸露持倉。
    4. 數量嚴格守恆: 30% TP1 + 30% TP2 + 40% 尾倉 = 100% 原倉位。
    5. 空單完全對稱。
    """

    @staticmethod
    def match_long_bar(
        b_open: float,
        b_high: float,
        b_low: float,
        b_close: float,
        pos: Dict[str, Any],
        taker_fee_rate: float = 0.0005,
        slippage_rate: float = 0.0005
    ) -> Dict[str, Any]:
        entry_p = pos["entry_price"]
        r_dist = pos["r_distance"]
        orig_qty = pos["orig_qty"]
        curr_sl = pos["current_sl"]
        tp1_p = pos["tp1_price"]
        tp2_p = pos["tp2_price"]
        tp1_done = pos["tp1_done"]
        tp2_done = pos["tp2_done"]
        rem_qty = pos["remaining_qty"]

        res = {
            "closed": False,
            "exit_reason": "",
            "exit_price": 0.0,
            "realized_gross_pnl": 0.0,
            "realized_fee": 0.0,
            "realized_slip": 0.0,
            "realized_net_pnl": 0.0,
            "realized_pnl_delta": 0.0,
            "pos": pos,
        }

        # 1. 停損優先 (涵蓋跳空低開: 若 b_open < curr_sl 則以更差的 b_open 成交)
        if b_low <= curr_sl:
            exec_p = min(b_open, curr_sl)
            raw_pnl = (exec_p - entry_p) * rem_qty
            fee = (entry_p + exec_p) * rem_qty * taker_fee_rate
            slip = (entry_p + exec_p) * rem_qty * slippage_rate
            net_pnl = raw_pnl - fee - slip

            pos["remaining_qty"] = 0.0
            res["closed"] = True
            res["exit_price"] = exec_p
            if tp2_done:
                res["exit_reason"] = "TP2後_尾倉移動停損觸發"
            elif tp1_done:
                res["exit_reason"] = "TP1後_保本停損觸發"
            else:
                res["exit_reason"] = "初始結構停損觸發"

            res["realized_gross_pnl"] = raw_pnl
            res["realized_fee"] = fee
            res["realized_slip"] = slip
            res["realized_net_pnl"] = net_pnl
            res["realized_pnl_delta"] = net_pnl
            return res

        # 2. 檢查 TP1
        if not tp1_done:
            if b_high >= tp1_p:
                if b_low <= entry_p:
                    # 悲觀認定: 同根回踩跌破進場價，作廢 TP1，全倉以進場價保本離場 (若跳空低開以 b_open 成交)
                    exec_p = min(b_open, entry_p) if b_open <= entry_p else entry_p
                    raw_pnl = (exec_p - entry_p) * rem_qty
                    fee = (entry_p + exec_p) * rem_qty * taker_fee_rate
                    slip = (entry_p + exec_p) * rem_qty * slippage_rate
                    net_pnl = raw_pnl - fee - slip

                    pos["remaining_qty"] = 0.0
                    res["closed"] = True
                    res["exit_price"] = exec_p
                    res["exit_reason"] = "TP1與保本同根觸碰_悲觀保本平倉"
                    res["realized_gross_pnl"] = raw_pnl
                    res["realized_fee"] = fee
                    res["realized_slip"] = slip
                    res["realized_net_pnl"] = net_pnl
                    res["realized_pnl_delta"] = net_pnl
                    return res
                else:
                    # 順利達成 TP1 (精確平倉 30%)
                    close_qty = orig_qty * 0.30
                    raw_pnl = (tp1_p - entry_p) * close_qty
                    fee = (entry_p + tp1_p) * close_qty * taker_fee_rate
                    slip = (entry_p + tp1_p) * close_qty * slippage_rate
                    net_pnl = raw_pnl - fee - slip

                    rem_qty -= close_qty
                    pos["remaining_qty"] = rem_qty
                    pos["tp1_done"] = True
                    curr_sl = entry_p  # 停損移至保本
                    pos["current_sl"] = curr_sl

                    res["realized_gross_pnl"] += raw_pnl
                    res["realized_fee"] += fee
                    res["realized_slip"] += slip
                    res["realized_net_pnl"] += net_pnl
                    res["realized_pnl_delta"] = res["realized_net_pnl"]

        # 3. 檢查 TP2
        if pos["tp1_done"] and not pos["tp2_done"]:
            if b_high >= tp2_p:
                close_qty = orig_qty * 0.30
                raw_pnl = (tp2_p - entry_p) * close_qty
                fee = (entry_p + tp2_p) * close_qty * taker_fee_rate
                slip = (entry_p + tp2_p) * close_qty * slippage_rate
                net_pnl = raw_pnl - fee - slip

                rem_qty -= close_qty
                pos["remaining_qty"] = rem_qty
                pos["tp2_done"] = True
                new_sl = entry_p + (1.0 * r_dist)  # 鎖定 +1R

                res["realized_gross_pnl"] += raw_pnl
                res["realized_fee"] += fee
                res["realized_slip"] += slip
                res["realized_net_pnl"] += net_pnl
                res["realized_pnl_delta"] = res["realized_net_pnl"]

                # 檢查新停損是否同根被貫穿
                if b_low <= new_sl:
                    exec_p = min(b_open, new_sl) if b_open <= new_sl else new_sl
                    raw_pnl_runner = (exec_p - entry_p) * rem_qty
                    fee_runner = (entry_p + exec_p) * rem_qty * taker_fee_rate
                    slip_runner = (entry_p + exec_p) * rem_qty * slippage_rate
                    net_pnl_runner = raw_pnl_runner - fee_runner - slip_runner

                    pos["remaining_qty"] = 0.0
                    pos["current_sl"] = new_sl
                    res["closed"] = True
                    res["exit_price"] = exec_p
                    res["exit_reason"] = "TP2後同根回踩+1R移動停利"
                    res["realized_gross_pnl"] += raw_pnl_runner
                    res["realized_fee"] += fee_runner
                    res["realized_slip"] += slip_runner
                    res["realized_net_pnl"] += net_pnl_runner
                    res["realized_pnl_delta"] = res["realized_net_pnl"]
                    return res
                else:
                    curr_sl = new_sl
                    pos["current_sl"] = curr_sl

        return res

    @staticmethod
    def match_short_bar(
        b_open: float,
        b_high: float,
        b_low: float,
        b_close: float,
        pos: Dict[str, Any],
        taker_fee_rate: float = 0.0005,
        slippage_rate: float = 0.0005
    ) -> Dict[str, Any]:
        entry_p = pos["entry_price"]
        r_dist = pos["r_distance"]
        orig_qty = pos["orig_qty"]
        curr_sl = pos["current_sl"]
        tp1_p = pos["tp1_price"]
        tp2_p = pos["tp2_price"]
        tp1_done = pos["tp1_done"]
        tp2_done = pos["tp2_done"]
        rem_qty = pos["remaining_qty"]

        res = {
            "closed": False,
            "exit_reason": "",
            "exit_price": 0.0,
            "realized_gross_pnl": 0.0,
            "realized_fee": 0.0,
            "realized_slip": 0.0,
            "realized_net_pnl": 0.0,
            "realized_pnl_delta": 0.0,
            "pos": pos,
        }

        # 1. 停損優先 (涵蓋跳空高開: 若 b_open > curr_sl 則以更差的 b_open 成交)
        if b_high >= curr_sl:
            exec_p = max(b_open, curr_sl)
            raw_pnl = (entry_p - exec_p) * rem_qty
            fee = (entry_p + exec_p) * rem_qty * taker_fee_rate
            slip = (entry_p + exec_p) * rem_qty * slippage_rate
            net_pnl = raw_pnl - fee - slip

            pos["remaining_qty"] = 0.0
            res["closed"] = True
            res["exit_price"] = exec_p
            if tp2_done:
                res["exit_reason"] = "TP2後_尾倉移動停損觸發"
            elif tp1_done:
                res["exit_reason"] = "TP1後_保本停損觸發"
            else:
                res["exit_reason"] = "初始結構停損觸發"

            res["realized_gross_pnl"] = raw_pnl
            res["realized_fee"] = fee
            res["realized_slip"] = slip
            res["realized_net_pnl"] = net_pnl
            res["realized_pnl_delta"] = net_pnl
            return res

        # 2. 檢查 TP1
        if not tp1_done:
            if b_low <= tp1_p:
                if b_high >= entry_p:
                    exec_p = max(b_open, entry_p) if b_open >= entry_p else entry_p
                    raw_pnl = (entry_p - exec_p) * rem_qty
                    fee = (entry_p + exec_p) * rem_qty * taker_fee_rate
                    slip = (entry_p + exec_p) * rem_qty * slippage_rate
                    net_pnl = raw_pnl - fee - slip

                    pos["remaining_qty"] = 0.0
                    res["closed"] = True
                    res["exit_price"] = exec_p
                    res["exit_reason"] = "TP1與保本同根觸碰_悲觀保本平倉"
                    res["realized_gross_pnl"] = raw_pnl
                    res["realized_fee"] = fee
                    res["realized_slip"] = slip
                    res["realized_net_pnl"] = net_pnl
                    res["realized_pnl_delta"] = net_pnl
                    return res
                else:
                    close_qty = orig_qty * 0.30
                    raw_pnl = (entry_p - tp1_p) * close_qty
                    fee = (entry_p + tp1_p) * close_qty * taker_fee_rate
                    slip = (entry_p + tp1_p) * close_qty * slippage_rate
                    net_pnl = raw_pnl - fee - slip

                    rem_qty -= close_qty
                    pos["remaining_qty"] = rem_qty
                    pos["tp1_done"] = True
                    curr_sl = entry_p  # 停損移至保本
                    pos["current_sl"] = curr_sl

                    res["realized_gross_pnl"] += raw_pnl
                    res["realized_fee"] += fee
                    res["realized_slip"] += slip
                    res["realized_net_pnl"] += net_pnl
                    res["realized_pnl_delta"] = res["realized_net_pnl"]

        # 3. 檢查 TP2
        if pos["tp1_done"] and not pos["tp2_done"]:
            if b_low <= tp2_p:
                close_qty = orig_qty * 0.30
                raw_pnl = (entry_p - tp2_p) * close_qty
                fee = (entry_p + tp2_p) * close_qty * taker_fee_rate
                slip = (entry_p + tp2_p) * close_qty * slippage_rate
                net_pnl = raw_pnl - fee - slip

                rem_qty -= close_qty
                pos["remaining_qty"] = rem_qty
                pos["tp2_done"] = True
                new_sl = entry_p - (1.0 * r_dist)

                res["realized_gross_pnl"] += raw_pnl
                res["realized_fee"] += fee
                res["realized_slip"] += slip
                res["realized_net_pnl"] += net_pnl
                res["realized_pnl_delta"] = res["realized_net_pnl"]

                if b_high >= new_sl:
                    exec_p = max(b_open, new_sl) if b_open >= new_sl else new_sl
                    raw_pnl_runner = (entry_p - exec_p) * rem_qty
                    fee_runner = (entry_p + exec_p) * rem_qty * taker_fee_rate
                    slip_runner = (entry_p + exec_p) * rem_qty * slippage_rate
                    net_pnl_runner = raw_pnl_runner - fee_runner - slip_runner

                    pos["remaining_qty"] = 0.0
                    pos["current_sl"] = new_sl
                    res["closed"] = True
                    res["exit_price"] = exec_p
                    res["exit_reason"] = "TP2後同根回踩+1R移動停利"
                    res["realized_gross_pnl"] += raw_pnl_runner
                    res["realized_fee"] += fee_runner
                    res["realized_slip"] += slip_runner
                    res["realized_net_pnl"] += net_pnl_runner
                    res["realized_pnl_delta"] = res["realized_net_pnl"]
                    return res
                else:
                    curr_sl = new_sl
                    pos["current_sl"] = curr_sl

        return res
