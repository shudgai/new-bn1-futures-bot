"""
core/analytics/trade_accounting.py
==================================
Binance Futures Bot 2.0 嚴格合約帳務與損益結算稽核模組

確保：
1. 每筆交易的金額 (USDT)、名目本金 (Notional)、保證金 (Margin) 與槓桿 (Leverage) 口徑嚴格一致。
2. 徹底消除「滑點既反映在成交價又在手續費重複加總」的重複計費錯誤。
3. 嚴格滿足恆等式：
   Net PnL (USDT) = Raw Gross PnL (USDT) - Total Friction Cost (USDT)
   Net Nom PnL %  = Raw Gross Nom %      - Total Friction Nom %
4. 明確區分名目報酬率 (Unleveraged)、保證金報酬率 (ROE) 與帳戶權益報酬率 (Portfolio Return)。
"""

import math
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np


@dataclass
class AuditedTrade:
    symbol: str
    exp_name: str
    period: str                     # "IN_SAMPLE" or "OUT_OF_SAMPLE"
    side: str                       # "LONG" or "SHORT"
    entry_mode: str
    entry_time: Any
    exit_time: Any
    hold_bars: int
    exit_reason: str

    # 價格欄位
    p_entry_raw: float              # 原始進場信號價 (未計滑點)
    p_entry_exec: float             # 實際進場成交價 (含進場滑點)
    p_exit_raw: float               # 原始出場信號價 (未計滑點)
    p_exit_exec: float              # 實際出場成交價 (含出場滑點)

    # 部位與保證金
    qty: float                      # 成交標的數量
    notional_entry: float           # 進場名目價值 (USDT)
    notional_exit: float            # 出場名目價值 (USDT)
    leverage: float                 # 槓桿倍數 (例如 5.0)
    margin: float                   # 實際佔用保證金 (USDT)

    # 金額口徑損益 (USDT Cash Flow)
    raw_gross_pnl_usd: float        # 純毛損益金額 (未扣任何手續費與滑點)
    entry_slip_usd: float           # 進場滑點成本金額
    exit_slip_usd: float            # 出場滑點成本金額
    total_slip_usd: float           # 雙邊滑點總金額
    entry_fee_usd: float            # 進場手續費金額 (按進場名目成交額計算)
    exit_fee_usd: float             # 出場手續費金額 (按出場名目成交額計算)
    total_fee_usd: float            # 雙邊手續費總金額
    funding_fee_usd: float          # 資金費率 (未模擬跨8小時結算標記為 0.0)
    total_cost_usd: float           # 已模擬之全部摩擦成本 (滑點 + 手續費 + 資金費)
    net_pnl_usd: float              # 扣除全部摩擦成本後之淨損益金額

    # 百分比口徑 (分母嚴格明確)
    nom_gross_pct: float            # 以進場名目本金為分母之毛報酬率 (%)
    nom_fee_pct: float              # 以進場名目本金為分母之手續費比例 (%)
    nom_slip_pct: float             # 以進場名目本金為分母之滑點比例 (%)
    nom_cost_pct: float             # 以進場名目本金為分母之總摩擦比例 (%)
    nom_net_pct: float              # 以進場名目本金為分母之淨報酬率 (%)
    roe_net_pct: float              # 以佔用保證金為分母之淨 ROE (%)

    # 帳戶權益追蹤 (單利與複利)
    equity_before: float            # 該筆交易前之帳戶權益
    equity_after: float             # 該筆交易後之帳戶權益
    portfolio_return_pct: float     # 對當前帳戶權益之貢獻率 (%)

    # 風險指標
    mfe_pct: float                  # 最大有利波動 (%)
    mae_pct: float                  # 最大不利波動 (%)
    atr_entry: float                # 進場時 ATR
    sl_dist_pct: float              # 止損距離佔進場價之百分比
    resumed_after_sl: bool          # 止損後是否恢復原方向

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TradeAccountingEngine:
    @staticmethod
    def settle_trade(
        symbol: str,
        exp_name: str,
        period: str,
        side: str,
        entry_mode: str,
        entry_time: Any,
        exit_time: Any,
        hold_bars: int,
        exit_reason: str,
        p_entry_raw: float,
        p_exit_raw: float,
        fee_rate: float = 0.0004,
        slippage_rate: float = 0.0002,
        notional_target: float = 1000.0,
        leverage: float = 5.0,
        current_equity: float = 10000.0,
        mfe_raw: float = 0.0,
        mae_raw: float = 0.0,
        atr_entry: float = 0.0,
        initial_sl: float = 0.0,
        resumed_after_sl: bool = False,
    ) -> AuditedTrade:
        """
        以標準交易所撮合與結算邏輯執行單筆交易稽核計算
        """
        # 1. 成交價 (加計滑點)
        if side == "LONG":
            p_entry_exec = p_entry_raw * (1.0 + slippage_rate)
            p_exit_exec = p_exit_raw * (1.0 - slippage_rate)
        else:
            p_entry_exec = p_entry_raw * (1.0 - slippage_rate)
            p_exit_exec = p_exit_raw * (1.0 + slippage_rate)

        # 2. 部位數量與名目價值
        qty = notional_target / p_entry_raw
        notional_entry = qty * p_entry_raw
        notional_exit = qty * p_exit_raw
        margin = notional_target / leverage

        # 3. 未扣任何成本之純毛損益金額 (Raw Gross PnL)
        if side == "LONG":
            raw_gross_pnl_usd = (p_exit_raw - p_entry_raw) * qty
        else:
            raw_gross_pnl_usd = (p_entry_raw - p_exit_raw) * qty

        # 4. 滑點金額
        entry_slip_usd = abs(p_entry_exec - p_entry_raw) * qty
        exit_slip_usd = abs(p_exit_exec - p_exit_raw) * qty
        total_slip_usd = entry_slip_usd + exit_slip_usd

        # 5. 手續費金額 (按實際成交名目價值計算)
        entry_fee_usd = (qty * p_entry_exec) * fee_rate
        exit_fee_usd = (qty * p_exit_exec) * fee_rate
        total_fee_usd = entry_fee_usd + exit_fee_usd

        # 6. 資金費 (高頻短線平均持倉 30m，未跨 8 小時標記為 0.0)
        funding_fee_usd = 0.0

        # 7. 總成本與淨損益
        total_cost_usd = total_slip_usd + total_fee_usd + funding_fee_usd
        net_pnl_usd = raw_gross_pnl_usd - total_cost_usd

        # 8. 嚴格恆等式檢驗 (保證無漂移)
        # 驗證 Net PnL 是否亦完全等於 (Exec PnL - Fees)
        if side == "LONG":
            exec_pnl_usd = (p_exit_exec - p_entry_exec) * qty
        else:
            exec_pnl_usd = (p_entry_exec - p_exit_exec) * qty
        alt_net = exec_pnl_usd - total_fee_usd - funding_fee_usd
        assert math.isclose(net_pnl_usd, alt_net, rel_tol=1e-5, abs_tol=1e-5), (
            f"帳務不一致! raw_gross - total_cost = {net_pnl_usd}, exec_pnl - fee = {alt_net}"
        )

        # 9. 百分比口徑計算 (分母皆為進場名目價值 notional_target)
        nom_gross_pct = (raw_gross_pnl_usd / notional_target) * 100.0
        nom_fee_pct = (total_fee_usd / notional_target) * 100.0
        nom_slip_pct = (total_slip_usd / notional_target) * 100.0
        nom_cost_pct = (total_cost_usd / notional_target) * 100.0
        nom_net_pct = (net_pnl_usd / notional_target) * 100.0

        # 恆等式檢驗: nom_gross_pct - nom_cost_pct == nom_net_pct
        assert math.isclose(nom_gross_pct - nom_cost_pct, nom_net_pct, abs_tol=1e-5), (
            f"百分比不一致! gross ({nom_gross_pct}) - cost ({nom_cost_pct}) != net ({nom_net_pct})"
        )

        # 保證金報酬率 (ROE %)
        roe_net_pct = (net_pnl_usd / margin) * 100.0

        # 帳戶權益變化
        equity_before = current_equity
        equity_after = current_equity + net_pnl_usd
        portfolio_return_pct = (net_pnl_usd / equity_before) * 100.0

        # 風險指標
        mfe_pct = (mfe_raw / p_entry_raw) * 100.0 if p_entry_raw > 0 else 0.0
        mae_pct = (mae_raw / p_entry_raw) * 100.0 if p_entry_raw > 0 else 0.0
        sl_dist_pct = (abs(p_entry_raw - initial_sl) / p_entry_raw) * 100.0 if p_entry_raw > 0 else 0.0

        return AuditedTrade(
            symbol=symbol,
            exp_name=exp_name,
            period=period,
            side=side,
            entry_mode=entry_mode,
            entry_time=entry_time,
            exit_time=exit_time,
            hold_bars=hold_bars,
            exit_reason=exit_reason,
            p_entry_raw=float(p_entry_raw),
            p_entry_exec=float(p_entry_exec),
            p_exit_raw=float(p_exit_raw),
            p_exit_exec=float(p_exit_exec),
            qty=float(qty),
            notional_entry=float(notional_entry),
            notional_exit=float(notional_exit),
            leverage=float(leverage),
            margin=float(margin),
            raw_gross_pnl_usd=float(raw_gross_pnl_usd),
            entry_slip_usd=float(entry_slip_usd),
            exit_slip_usd=float(exit_slip_usd),
            total_slip_usd=float(total_slip_usd),
            entry_fee_usd=float(entry_fee_usd),
            exit_fee_usd=float(exit_fee_usd),
            total_fee_usd=float(total_fee_usd),
            funding_fee_usd=float(funding_fee_usd),
            total_cost_usd=float(total_cost_usd),
            net_pnl_usd=float(net_pnl_usd),
            nom_gross_pct=float(nom_gross_pct),
            nom_fee_pct=float(nom_fee_pct),
            nom_slip_pct=float(nom_slip_pct),
            nom_cost_pct=float(nom_cost_pct),
            nom_net_pct=float(nom_net_pct),
            roe_net_pct=float(roe_net_pct),
            equity_before=float(equity_before),
            equity_after=float(equity_after),
            portfolio_return_pct=float(portfolio_return_pct),
            mfe_pct=float(mfe_pct),
            mae_pct=float(mae_pct),
            atr_entry=float(atr_entry),
            sl_dist_pct=float(sl_dist_pct),
            resumed_after_sl=resumed_after_sl,
        )


class PortfolioAccountingEngine:
    @staticmethod
    def evaluate_portfolio(
        trades: List[Dict[str, Any]],
        initial_equity: float = 10000.0,
    ) -> Dict[str, Any]:
        """
        跨標的多資產組合時間序列嚴格帳務結算器：
        1. 嚴格按出場時間排序。
        2. 防重入檢驗，確保無重複交易入帳。
        3. 計算精確的期末權益、總報酬率與組合最大回撤 (Portfolio MDD)。
        4. 分析歷史最大同時持倉標的數與最大保證金占用。
        5. 恆等式保證：
           - 期末權益 == 初始權益 + 全部已結算淨損益 (net_pnl_usd)
           - 總報酬率 == (期末權益 / 初始權益 - 1) * 100%
        """
        if not trades:
            return {
                "initial_equity": initial_equity,
                "final_equity": initial_equity,
                "portfolio_ret_pct": 0.0,
                "mdd_pct": 0.0,
                "total_net_pnl_usd": 0.0,
                "max_concurrent_positions": 0,
                "max_margin_used_usd": 0.0,
                "max_margin_ratio_pct": 0.0,
                "audited_trades_sorted": [],
                "equity_series": [initial_equity],
            }

        df_t = pd.DataFrame(trades).copy()

        # 1. 重複入帳檢驗
        dup_mask = df_t.duplicated(subset=['symbol', 'entry_time', 'exit_time', 'side'])
        assert dup_mask.sum() == 0, f"偵測到重複交易入帳: {dup_mask.sum()} 筆!"

        # 2. 嚴格時間順序排序 (以 exit_time 結算時間為準，次序依 entry_time、symbol)
        df_t['dt_exit'] = pd.to_datetime(df_t['exit_time'])
        df_t['dt_entry'] = pd.to_datetime(df_t['entry_time'])
        df_t = df_t.sort_values(by=['dt_exit', 'dt_entry', 'symbol']).reset_index(drop=True)

        # 3. 逐筆依時間序列累加損益，計算組合權益曲線
        total_net_pnl = float(df_t['net_pnl_usd'].sum())
        df_t['portfolio_equity_before'] = initial_equity + df_t['net_pnl_usd'].cumsum() - df_t['net_pnl_usd']
        df_t['portfolio_equity_after'] = initial_equity + df_t['net_pnl_usd'].cumsum()

        final_equity = float(df_t['portfolio_equity_after'].iloc[-1])

        # 4. 嚴格恆等式檢驗
        expected_final = initial_equity + total_net_pnl
        assert math.isclose(final_equity, expected_final, abs_tol=1e-4), (
            f"期末權益不一致! final_equity={final_equity}, initial+net_pnl={expected_final}"
        )

        portfolio_ret_pct = ((final_equity / initial_equity) - 1.0) * 100.0
        expected_ret = (total_net_pnl / initial_equity) * 100.0
        assert math.isclose(portfolio_ret_pct, expected_ret, abs_tol=1e-4), (
            f"總報酬率不一致! ret={portfolio_ret_pct}%, expected={expected_ret}%"
        )

        # 5. 依完整時間序列權益曲線計算組合 MDD
        eq_series = [initial_equity] + list(df_t['portfolio_equity_after'])
        s_eq = pd.Series(eq_series)
        s_peak = s_eq.cummax()
        s_dd = (s_eq - s_peak) / s_peak
        mdd_pct = float(abs(s_dd.min()) * 100.0)

        # 6. 同時持倉與保證金占用分析 (事件時間掃描)
        events = []
        for _, row in df_t.iterrows():
            margin = float(row.get('margin', row.get('notional_entry', 1000.0) / 5.0))
            events.append((row['dt_entry'], 1, row['symbol'], margin))
            events.append((row['dt_exit'], -1, row['symbol'], margin))

        # 依時間排序事件 (若時間相同，先出場 -1 後進場 +1)
        events.sort(key=lambda x: (x[0], x[1]))

        max_concurrent = 0
        curr_concurrent = 0
        max_margin = 0.0
        curr_margin = 0.0

        for t, event_type, sym, margin in events:
            curr_concurrent += event_type
            curr_margin += event_type * margin
            if curr_concurrent > max_concurrent:
                max_concurrent = curr_concurrent
            if curr_margin > max_margin:
                max_margin = curr_margin

        max_margin_ratio_pct = (max_margin / initial_equity) * 100.0

        return {
            "initial_equity": initial_equity,
            "final_equity": round(final_equity, 2),
            "final_equity_raw": final_equity,
            "portfolio_ret_pct": round(portfolio_ret_pct, 2),
            "portfolio_ret_pct_raw": portfolio_ret_pct,
            "mdd_pct": round(mdd_pct, 2),
            "total_net_pnl_usd": round(total_net_pnl, 2),
            "total_net_pnl_usd_raw": total_net_pnl,
            "max_concurrent_positions": max_concurrent,
            "max_margin_used_usd": round(max_margin, 2),
            "max_margin_ratio_pct": round(max_margin_ratio_pct, 2),
            "equity_series": eq_series,
        }


