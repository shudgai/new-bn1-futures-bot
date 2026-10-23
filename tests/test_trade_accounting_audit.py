"""
tests/test_trade_accounting_audit.py
====================================
嚴格合約帳務與損益結算一致性自動化測試套件 (第四階段核心要求)
驗證項目：
1. 多空方向毛損益符號與數量邏輯 (多單漲賺跌賠、空單跌賺漲賠)
2. 手續費按實際成交名目價值精確計算 (開平各自計費)
3. 滑點不重複計算，滿足 Net PnL = Raw Gross PnL - Total Friction Cost
4. 淨損益恆等式在每一筆交易及彙總層級 100% 成立
5. 名目報酬率、保證金 ROE 與帳戶權益報酬率分母正確分離
6. 複利權益曲線與最大回撤計算一致性
"""

import math
import pytest
import pandas as pd
import numpy as np
from core.analytics.trade_accounting import TradeAccountingEngine, AuditedTrade


def test_long_accounting_gain():
    """多單獲利帳務核算測試"""
    trade = TradeAccountingEngine.settle_trade(
        symbol="BTCUSDT",
        exp_name="TestExp",
        period="OUT_OF_SAMPLE",
        side="LONG",
        entry_mode="MODE_2_PULLBACK",
        entry_time="2026-10-21 10:00:00",
        exit_time="2026-10-21 10:30:00",
        hold_bars=10,
        exit_reason="EARLY_PIVOT_PEAK_EXIT",
        p_entry_raw=80000.0,
        p_exit_raw=80800.0,       # 原始上漲 +1.0%
        fee_rate=0.0004,          # 0.04%
        slippage_rate=0.0002,     # 0.02%
        notional_target=1000.0,
        leverage=5.0,
        current_equity=10000.0,
    )

    # 1. 數量與名目本金
    assert math.isclose(trade.qty, 1000.0 / 80000.0)
    assert math.isclose(trade.notional_entry, 1000.0)
    assert math.isclose(trade.margin, 200.0)

    # 2. 純毛損益 (1000 USD * +1.0% = +10.0 USD)
    assert math.isclose(trade.raw_gross_pnl_usd, 10.0, abs_tol=1e-4)
    assert math.isclose(trade.nom_gross_pct, 1.0, abs_tol=1e-4)

    # 3. 滑點成本
    # 進場滑點 0.02% = 0.20 USD，出場滑點 0.02% * (80800/80000) * 1000 = 0.2016 USD
    assert trade.total_slip_usd > 0.40

    # 4. 手續費成本 (開平各 0.04%)
    assert trade.total_fee_usd > 0.80

    # 5. 核心恆等式: Raw Gross - Total Cost == Net PnL
    assert math.isclose(
        trade.raw_gross_pnl_usd - trade.total_cost_usd,
        trade.net_pnl_usd,
        abs_tol=1e-4
    )
    # 百分比恆等式: nom_gross_pct - nom_cost_pct == nom_net_pct
    assert math.isclose(
        trade.nom_gross_pct - trade.nom_cost_pct,
        trade.nom_net_pct,
        abs_tol=1e-4
    )

    # 6. 保證金 ROE 必須等於 Net PnL / Margin
    assert math.isclose(trade.roe_net_pct, (trade.net_pnl_usd / 200.0) * 100.0, abs_tol=1e-4)


def test_short_accounting_gain():
    """空單獲利帳務核算測試"""
    trade = TradeAccountingEngine.settle_trade(
        symbol="ETHUSDT",
        exp_name="TestExp",
        period="OUT_OF_SAMPLE",
        side="SHORT",
        entry_mode="MODE_2_PULLBACK",
        entry_time="2026-10-21 11:00:00",
        exit_time="2026-10-21 11:45:00",
        hold_bars=15,
        exit_reason="EARLY_PIVOT_VALLEY_EXIT",
        p_entry_raw=2500.0,
        p_exit_raw=2450.0,        # 原始下跌 -2.0% (空單賺 +2.0%)
        fee_rate=0.0004,
        slippage_rate=0.0002,
        notional_target=1000.0,
        leverage=5.0,
        current_equity=10000.0,
    )

    # 空單下跌，毛損益必須為正
    assert trade.raw_gross_pnl_usd > 0
    assert math.isclose(trade.nom_gross_pct, 2.0, abs_tol=1e-4)

    # 核心恆等式檢查
    assert math.isclose(
        trade.raw_gross_pnl_usd - trade.total_cost_usd,
        trade.net_pnl_usd,
        abs_tol=1e-4
    )
    assert math.isclose(
        trade.nom_gross_pct - trade.nom_cost_pct,
        trade.nom_net_pct,
        abs_tol=1e-4
    )


def test_long_accounting_loss():
    """多單虧損止損帳務核算測試"""
    trade = TradeAccountingEngine.settle_trade(
        symbol="SOLUSDT",
        exp_name="TestExp",
        period="OUT_OF_SAMPLE",
        side="LONG",
        entry_mode="MODE_3_BREAKOUT",
        entry_time="2026-10-21 12:00:00",
        exit_time="2026-10-21 12:15:00",
        hold_bars=5,
        exit_reason="SL_STRUCTURAL_INTRABAR",
        p_entry_raw=100.0,
        p_exit_raw=99.0,          # 跌 -1.0%
        fee_rate=0.0004,
        slippage_rate=0.0002,
        notional_target=1000.0,
        leverage=5.0,
        current_equity=10000.0,
    )

    assert trade.raw_gross_pnl_usd < 0
    assert math.isclose(trade.nom_gross_pct, -1.0, abs_tol=1e-4)

    # 淨損益虧損必定大於純毛損 (因為扣除手續費與滑點)
    assert trade.net_pnl_usd < trade.raw_gross_pnl_usd
    assert math.isclose(
        trade.raw_gross_pnl_usd - trade.total_cost_usd,
        trade.net_pnl_usd,
        abs_tol=1e-4
    )


def test_aggregate_accounting_invariance():
    """測試多筆交易加總與帳戶彙總恆等式 (解決第三階段報告矛盾)"""
    trades = []
    eq = 10000.0
    for i in range(20):
        side = "LONG" if i % 2 == 0 else "SHORT"
        p_in = 100.0 + i
        # 模擬部分獲利、部分虧損
        move = 0.015 if i % 3 == 0 else -0.01
        p_out = p_in * (1.0 + move) if side == "LONG" else p_in * (1.0 - move)

        t = TradeAccountingEngine.settle_trade(
            symbol="BTCUSDT",
            exp_name="BatchTest",
            period="OUT_OF_SAMPLE",
            side=side,
            entry_mode="MODE_2_PULLBACK",
            entry_time=f"2026-10-21 10:{i:02d}:00",
            exit_time=f"2026-10-21 11:{i:02d}:00",
            hold_bars=10,
            exit_reason="TEST",
            p_entry_raw=p_in,
            p_exit_raw=p_out,
            fee_rate=0.0004,
            slippage_rate=0.0002,
            notional_target=1000.0,
            leverage=5.0,
            current_equity=eq,
        )
        trades.append(t)
        eq = t.equity_after

    df = pd.DataFrame([t.to_dict() for t in trades])

    total_gross = df['raw_gross_pnl_usd'].sum()
    total_cost = df['total_cost_usd'].sum()
    total_net = df['net_pnl_usd'].sum()

    # 彙總金額恆等式必須 100% 成立
    assert math.isclose(total_gross - total_cost, total_net, abs_tol=1e-4)

    # 彙總名目百分比恆等式必須 100% 成立
    tot_nom_gross = df['nom_gross_pct'].sum()
    tot_nom_cost = df['nom_cost_pct'].sum()
    tot_nom_net = df['nom_net_pct'].sum()
    assert math.isclose(tot_nom_gross - tot_nom_cost, tot_nom_net, abs_tol=1e-4)

    # 終端帳戶權益必須嚴格等於初始本金 + 總淨利
    assert math.isclose(10000.0 + total_net, eq, abs_tol=1e-4)


def test_portfolio_multi_asset_equity_curve_and_mdd():
    """
    第五階段專用測試：
    驗證多資產時間序列組合權益曲線、期末權益與最大回撤 (MDD)：
    1. 期末權益 == 初始權益 + 全部已結算淨損益 (net_pnl_usd)
    2. 總報酬率 == (期末權益 / 初始權益 - 1) * 100%
    3. 最大回撤由完整、依時間排序的權益曲線計算
    """
    from core.analytics.trade_accounting import PortfolioAccountingEngine

    # 模擬 3 個不同標的交錯進出場的交易
    sample_trades = [
        # BTC 多單淨賺 50 USD，10:00 出場
        {
            "symbol": "BTCUSDT", "side": "LONG", "entry_time": "2026-10-21 09:00:00",
            "exit_time": "2026-10-21 10:00:00", "net_pnl_usd": 50.0, "margin": 200.0, "notional_entry": 1000.0
        },
        # ETH 空單淨賠 120 USD，10:30 出場
        {
            "symbol": "ETHUSDT", "side": "SHORT", "entry_time": "2026-10-21 09:30:00",
            "exit_time": "2026-10-21 10:30:00", "net_pnl_usd": -120.0, "margin": 200.0, "notional_entry": 1000.0
        },
        # SOL 多單淨賺 30 USD，11:00 出場
        {
            "symbol": "SOLUSDT", "side": "LONG", "entry_time": "2026-10-21 09:45:00",
            "exit_time": "2026-10-21 11:00:00", "net_pnl_usd": 30.0, "margin": 200.0, "notional_entry": 1000.0
        },
    ]

    initial_eq = 10000.0
    res = PortfolioAccountingEngine.evaluate_portfolio(sample_trades, initial_equity=initial_eq)

    # 總淨損益 = 50 - 120 + 30 = -40 USD
    assert math.isclose(res['total_net_pnl_usd'], -40.0, abs_tol=1e-4)

    # 1. 期末權益 = 初始權益 + 全部已結算淨損益 = 10000 - 40 = 9960 USD
    assert math.isclose(res['final_equity'], 9960.0, abs_tol=1e-4)

    # 2. 總報酬率 = (期末權益 / 初始權益 - 1) * 100% = -0.40%
    assert math.isclose(res['portfolio_ret_pct'], -0.40, abs_tol=1e-4)

    # 3. 淨值走勢：10000 -> 10050 (峰值) -> 9930 (波谷，回撤 (9930-10050)/10050 = -1.194%) -> 9960
    # MDD 應約為 1.19%
    expected_mdd = ((10050.0 - 9930.0) / 10050.0) * 100.0
    assert math.isclose(res['mdd_pct'], expected_mdd, abs_tol=0.01)

    # 4. 同時持倉檢驗：在 09:45 時，BTC、ETH、SOL 三者皆在持倉中，最大同時持倉數應為 3
    assert res['max_concurrent_positions'] == 3
    assert math.isclose(res['max_margin_used_usd'], 600.0, abs_tol=1e-4)
    assert math.isclose(res['max_margin_ratio_pct'], 6.0, abs_tol=1e-4)


def test_portfolio_prevents_duplicate_trades():
    """驗證組合結算器能防止同一筆交易重複入帳"""
    from core.analytics.trade_accounting import PortfolioAccountingEngine

    dup_trades = [
        {
            "symbol": "BTCUSDT", "side": "LONG", "entry_time": "2026-10-21 09:00:00",
            "exit_time": "2026-10-21 10:00:00", "net_pnl_usd": 50.0, "margin": 200.0, "notional_entry": 1000.0
        },
        {
            "symbol": "BTCUSDT", "side": "LONG", "entry_time": "2026-10-21 09:00:00",
            "exit_time": "2026-10-21 10:00:00", "net_pnl_usd": 50.0, "margin": 200.0, "notional_entry": 1000.0
        },
    ]

    with pytest.raises(AssertionError, match="偵測到重複交易入帳"):
        PortfolioAccountingEngine.evaluate_portfolio(dup_trades, initial_equity=10000.0)

