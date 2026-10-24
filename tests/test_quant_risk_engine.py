"""
tests/test_quant_risk_engine.py
================================
驗證 QuantRiskEngine 量化風控與狀態機：
1. 淨利率計算（扣除 0.1% 費用）
2. 淨利未達 10% 時堅定持倉（趨勢保護）
3. 達標 10% 啟動鎖利機制
4. 利潤自高點回吐 30%（跌破 70% 水位）立即強制平倉
5. 趨勢反轉防守平倉
6. 開倉乖離過濾（防天花板追多、地板殺空）
7. 狀態日誌格式化輸出
"""
import pytest
from core.services.quant_risk_engine import (
    PositionState,
    KCChannels,
    QuantRiskEngine,
)


def test_profit_lock_not_active_under_10_percent():
    """未達 10% 淨利前，鎖利狀態待啟動，哪怕短暫回調依然堅決 HOLD"""
    pos = PositionState(
        symbol="BTC/USDT",
        side="LONG",
        entry_price=100.0,
        leverage=5.0,
        fee_estimate=0.001,
    )
    kc = KCChannels(
        middle=101.0,
        upper=103.0,
        lower=99.0,
        middle_slope=0.02,
        atr=2.0,
        trend="KC向上",
    )
    # 價格漲到 101.5: raw_ret = 1.5%, ROE = 7.5% - 0.1% = 7.4%
    decision = QuantRiskEngine.check_exit_rules(pos, current_price=101.5, kc=kc)
    assert decision["action"] == "HOLD"
    assert pos.is_lock_active is False
    assert pos.max_profit_pct > 0.07

    # 價格回踩到 101.1 (仍高於中軌 101.0)，利潤回撤，但未破結構且未達 10% 啟動線，必須續抱！
    decision_pullback = QuantRiskEngine.check_exit_rules(pos, current_price=101.1, kc=kc)
    assert decision_pullback["action"] == "HOLD"
    assert "多頭趨勢持續" in decision_pullback["reason"]


def test_profit_lock_activates_at_10_percent_and_triggers_at_30_percent_giveback():
    """
    淨利突破 10% 啟動鎖利模組，當利潤自最高點回吐 30% 時，立刻強制平倉！
    """
    pos = PositionState(
        symbol="ETH/USDT",
        side="LONG",
        entry_price=1000.0,
        leverage=5.0,
        fee_estimate=0.001,
    )
    kc = KCChannels(
        middle=1010.0,
        upper=1040.0,
        lower=980.0,
        middle_slope=0.05,
        atr=20.0,
        trend="KC向上",
    )
    # 價格衝高到 1025.0: raw_ret = 2.5%, ROE = 12.5% - 0.1% = 12.4% (>= 10.0%)
    decision_peak = QuantRiskEngine.check_exit_rules(pos, current_price=1025.0, kc=kc)
    assert pos.is_lock_active is True
    assert pos.max_profit_pct == pytest.approx(0.124, rel=1e-3)
    assert decision_peak["action"] == "HOLD"

    # 鎖利門檻線 = 12.4% * 0.70 = 8.68%
    # 當價格回吐到 1017.0: raw_ret = 1.7%, ROE = 8.5% - 0.1% = 8.4% (<= 8.68%)
    # 觸發 30% 回吐強制市價全平鎖利！
    decision_exit = QuantRiskEngine.check_exit_rules(pos, current_price=1017.0, kc=kc)
    assert decision_exit["action"] == "CLOSE"
    assert "【觸發30%回吐鎖利】" in decision_exit["reason"]
    assert "12.40%" in decision_exit["reason"]


def test_short_position_profit_lock():
    """空單 10% 啟動與 30% 回吐鎖利測試"""
    pos = PositionState(
        symbol="SOL/USDT",
        side="SHORT",
        entry_price=100.0,
        leverage=5.0,
        fee_estimate=0.001,
    )
    kc = KCChannels(
        middle=98.0,
        upper=102.0,
        lower=94.0,
        middle_slope=-0.05,
        atr=2.0,
        trend="KC向下",
    )
    # 暴跌到 97.0: raw_ret = 3.0%, ROE = 15.0% - 0.1% = 14.9% (>= 10.0%)
    decision_peak = QuantRiskEngine.check_exit_rules(pos, current_price=97.0, kc=kc)
    assert pos.is_lock_active is True
    assert decision_peak["action"] == "HOLD"

    # 鎖利底線 = 14.9% * 0.70 = 10.43%
    # 價格反彈到 97.9: raw_ret = 2.1%, ROE = 10.5% - 0.1% = 10.4% (<= 10.43%)
    decision_close = QuantRiskEngine.check_exit_rules(pos, current_price=97.9, kc=kc)
    assert decision_close["action"] == "CLOSE"
    assert "【觸發30%回吐鎖利】" in decision_close["reason"]


def test_trend_reversal_exit_when_not_locked():
    """未達 10% 鎖利，但趨勢結構正式破位時防守平倉"""
    pos = PositionState(
        symbol="BTC/USDT",
        side="LONG",
        entry_price=100.0,
        leverage=5.0,
        fee_estimate=0.001,
    )
    kc = KCChannels(
        middle=100.5,
        upper=102.0,
        lower=99.0,
        middle_slope=-0.03,  # 中軌已下傾
        atr=1.5,
        trend="KC向下",      # 趨勢已翻空
    )
    last_bar = {"open": 100.2, "high": 100.4, "low": 99.2, "close": 99.5}  # 跌破中軌收陰
    decision = QuantRiskEngine.check_exit_rules(pos, current_price=99.5, kc=kc, last_closed_bar=last_bar)
    assert decision["action"] == "CLOSE"
    assert "【趨勢反轉平倉】KC多頭結構破壞" in decision["reason"]


def test_entry_filter_rejects_ceiling_chase_and_allows_pullback_support():
    """驗證開倉過濾：天花板拒絕追多，回踩中軌支撐放行"""
    kc = KCChannels(
        middle=100.0,
        upper=102.0,
        lower=98.0,
        middle_slope=0.03,
        atr=1.0,
        trend="KC向上",
    )
    # 1. 天花板追多：價格高於 102.3 (upper + 0.3 ATR)
    ceiling_bar = {"open": 101.5, "high": 102.8, "low": 101.2, "close": 102.6}
    res_ceiling = QuantRiskEngine.check_entry_rules("BTC/USDT", kc, ceiling_bar, is_bar_closed=True)
    assert res_ceiling["action"] == "NONE"
    assert "【禁止天花板追多】" in res_ceiling["reason"]

    # 2. 順勢回踩中軌有守收紅陽線：放行開多
    support_bar = {"open": 100.1, "high": 100.8, "low": 99.9, "close": 100.6}
    res_support = QuantRiskEngine.check_entry_rules("BTC/USDT", kc, support_bar, is_bar_closed=True)
    assert res_support["action"] == "OPEN_LONG"


def test_status_output_formatting():
    """驗證標準化狀態輸出格式"""
    pos = PositionState(
        symbol="ETH/USDT",
        side="LONG",
        entry_price=2000.0,
        leverage=5.0,
    )
    kc = KCChannels(
        middle=2010.0,
        upper=2050.0,
        lower=1970.0,
        middle_slope=0.02,
        atr=20.0,
        trend="KC向上",
    )
    decision = QuantRiskEngine.check_exit_rules(pos, current_price=2030.0, kc=kc)
    formatted = QuantRiskEngine.format_status_output(decision)
    assert "【量化風控與執行狀態快照 - ETH/USDT】" in formatted
    assert "持倉狀態: LONG @ 2000.0000" in formatted
    assert "通道趨勢狀態: KC向上" in formatted
    assert "執行指令: 【HOLD】" in formatted
