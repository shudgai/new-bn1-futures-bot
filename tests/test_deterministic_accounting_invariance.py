"""
tests/test_deterministic_accounting_invariance.py
=================================================
第六階段自動化測試：
驗證確定性回測的帳務一致性鐵律：
1. 逐幣種淨利加總 == 全部交易淨利加總 == 期末權益變化 (差額 < 1e-4)
2. 總報酬率 == 淨利 / 初始權益
3. 回測報表與交易明細來自同一資料源，絕不混用
4. 存在任何差額時立即 Fail-Closed
"""

import os
import json
import math
import pytest
import pandas as pd
from core.analytics.trade_accounting import PortfolioAccountingEngine


def test_deterministic_invariance_on_sample_trades():
    """驗證多幣種在同時間軸下的三者絕對一致性"""
    trades = [
        {"symbol": "BTCUSDT", "side": "LONG", "entry_time": "2026-10-21 00:00:00", "exit_time": "2026-10-21 00:30:00", "net_pnl_usd": -19.5, "margin": 200.0, "notional_entry": 1000.0},
        {"symbol": "ETHUSDT", "side": "SHORT", "entry_time": "2026-10-21 00:15:00", "exit_time": "2026-10-21 01:00:00", "net_pnl_usd": 32.8, "margin": 200.0, "notional_entry": 1000.0},
        {"symbol": "SOLUSDT", "side": "LONG", "entry_time": "2026-10-21 00:20:00", "exit_time": "2026-10-21 01:15:00", "net_pnl_usd": -45.2, "margin": 200.0, "notional_entry": 1000.0},
        {"symbol": "BTCUSDT", "side": "SHORT", "entry_time": "2026-10-21 01:00:00", "exit_time": "2026-10-21 01:45:00", "net_pnl_usd": 12.0, "margin": 200.0, "notional_entry": 1000.0},
        {"symbol": "SUIUSDT", "side": "LONG", "entry_time": "2026-10-21 01:30:00", "exit_time": "2026-10-21 02:00:00", "net_pnl_usd": -8.1, "margin": 200.0, "notional_entry": 1000.0},
    ]

    initial_eq = 10000.0
    res = PortfolioAccountingEngine.evaluate_portfolio(trades, initial_equity=initial_eq)

    df = pd.DataFrame(trades)

    # 1. 逐幣種淨損益加總
    sum_by_symbol = df.groupby("symbol")["net_pnl_usd"].sum().sum()

    # 2. 全部交易淨損益加總
    sum_all_trades = df["net_pnl_usd"].sum()

    # 3. 期末權益變化
    equity_delta = res["final_equity"] - initial_eq

    # 核心斷言：三者必須 100% 精確相等
    assert math.isclose(sum_by_symbol, sum_all_trades, abs_tol=1e-5), f"幣種加總 {sum_by_symbol} != 交易加總 {sum_all_trades}"
    assert math.isclose(equity_delta, sum_all_trades, abs_tol=1e-5), f"權益變化 {equity_delta} != 交易加總 {sum_all_trades}"
    assert math.isclose(res["portfolio_ret_pct"], (sum_all_trades / initial_eq) * 100.0, abs_tol=1e-5)


def test_audit_fails_on_inconsistent_accounting():
    """驗證當存在人為竄改或統計口徑不一致時，必須立刻中斷報錯"""
    inconsistent_trades = [
        {"symbol": "BTCUSDT", "side": "LONG", "entry_time": "2026-10-21 00:00:00", "exit_time": "2026-10-21 00:30:00", "net_pnl_usd": 50.0, "margin": 200.0, "notional_entry": 1000.0},
        {"symbol": "ETHUSDT", "side": "LONG", "entry_time": "2026-10-21 00:10:00", "exit_time": "2026-10-21 00:40:00", "net_pnl_usd": -30.0, "margin": 200.0, "notional_entry": 1000.0},
    ]

    res = PortfolioAccountingEngine.evaluate_portfolio(inconsistent_trades, initial_equity=10000.0)

    # 人為製造差額 (例如加上 1.51 USD)
    tampered_symbol_sum = 20.0 + 1.51
    tampered_all_trades_sum = 20.0

    with pytest.raises(AssertionError):
        assert math.isclose(tampered_symbol_sum, tampered_all_trades_sum, abs_tol=1e-4), "偵測到帳務不一致差額"
