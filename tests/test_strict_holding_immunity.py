import pytest
from core.gates.strict_entry_and_immunity_gate import StrictEntryAndImmunityGate
from core.gates.trend_holding_immunity_gate import TrendHoldingImmunityGate

class MockHoldingProtectionService:
    """
    模擬出場核心保護邏輯：
    原則：開倉後，除『10%啟動+回吐20%鎖利』與『真峰谷突破/跌破MA15』之外，其餘一律不平倉。
    """
    def __init__(self, hard_stop_pct: float = -0.015):
        self.hard_stop_pct = hard_stop_pct

    def should_exit(self, side: str, bar: dict, indicators: dict, 
                    current_net_pnl: float, max_net_pnl: float) -> tuple[bool, str]:
        close = float(bar['close'])
        ma15 = float(indicators['ma15'])

        # 1. 絕對硬停損（破產保護，第一優先）
        if current_net_pnl <= self.hard_stop_pct:
            return True, "EXIT_HARD_STOP"

        # 2. 授權鎖利條件：淨利曾達 10%，且當前自最高利潤回吐達 20%
        if max_net_pnl >= 0.10:
            pullback_threshold = max_net_pnl * (1.0 - 0.20)
            if current_net_pnl <= pullback_threshold:
                return True, "EXIT_AUTHORIZED_PROFIT_LOCK"

        # 3. 真峰谷反轉（唯一常規放行條件：實體收盤跌破/站上 MA15）
        if side == 'LONG':
            if close < ma15:
                return True, "EXIT_TRUE_PIVOT_BROKEN_MA15"
            # 只要在 MA15 之上，無論十字星、反向K、碰軌道，一律 VETO 否決平倉！
            return False, "REJECT_EXIT: TREND_INTACT_ABOVE_MA15 (VETO)"

        elif side == 'SHORT':
            if close > ma15:
                return True, "EXIT_TRUE_PIVOT_BROKEN_MA15"
            # 只要在 MA15 之下，一律 VETO 否決平倉！
            return False, "REJECT_EXIT: TREND_INTACT_BELOW_MA15 (VETO)"

        return False, "UNKNOWN"


@pytest.fixture
def service():
    return MockHoldingProtectionService()


# ==============================================================================
# 測試場景 1：十字星與反向陰線（1000PEPE 痛點）—— 必須死抱不平倉！
# ==============================================================================
def test_long_doji_and_minor_pullback_must_not_exit(service):
    """多單拉升途中，出現十字星或小陰線，只要收在 MA15 之上，絕不平倉"""
    # 模擬 1000PEPE 衝到 0.004172，MA15 在 0.004165
    bar_doji = {
        'open': 0.004172,
        'high': 0.004175,
        'low': 0.004170,
        'close': 0.004171,  # 微幅收陰十字星
        'is_doji': True
    }
    indicators = {'ma15': 0.004165, 'kc_upper': 0.004170}
    
    # 利潤目前為 4% (未達 10% 啟動鎖利門檻)
    can_exit, reason = service.should_exit(
        side='LONG',
        bar=bar_doji,
        indicators=indicators,
        current_net_pnl=0.04,
        max_net_pnl=0.05
    )
    
    assert can_exit is False
    assert "VETO" in reason


# ==============================================================================
# 測試場景 2：觸碰 KC 上下軌道（Rail Touch）—— 必須死抱不平倉！
# ==============================================================================
def test_rail_touch_must_not_exit(service):
    """空單暴跌時觸碰 KC 下軌，未站上 MA15 之前嚴禁平倉提前落袋"""
    bar_touch_rail = {
        'open': 0.0435,
        'high': 0.0436,
        'low': 0.0428,
        'close': 0.0430
    }
    indicators = {'ma15': 0.0440, 'kc_lower': 0.0431} # 價格已穿透下軌
    
    can_exit, reason = service.should_exit(
        side='SHORT',
        bar=bar_touch_rail,
        indicators=indicators,
        current_net_pnl=0.06,
        max_net_pnl=0.07
    )
    
    assert can_exit is False
    assert "VETO" in reason


# ==============================================================================
# 測試場景 3：真峰谷確認（實體貫穿 MA15）—— 正式放行平倉！
# ==============================================================================
def test_true_pivot_cross_ma15_allows_exit(service):
    """多單實體收盤跌破 MA15，確認真峰頂反轉，放行平多離場"""
    bar_broken = {
        'open': 0.004168,
        'high': 0.004170,
        'low': 0.004158,
        'close': 0.004160  # 收盤摜破 MA15 (0.004165)
    }
    indicators = {'ma15': 0.004165}
    
    can_exit, reason = service.should_exit(
        side='LONG',
        bar=bar_broken,
        indicators=indicators,
        current_net_pnl=0.02,
        max_net_pnl=0.05
    )
    
    assert can_exit is True
    assert reason == "EXIT_TRUE_PIVOT_BROKEN_MA15"


# ==============================================================================
# 測試場景 4：授權鎖利（達 10% 啟動且利潤回吐 20%）—— 正式放行平倉！
# ==============================================================================
def test_authorized_profit_lock_exit(service):
    """淨利最高曾達 15%，回吐至 11.5%（回吐超過 20%），放行鎖利平倉"""
    bar_holding = {
        'open': 0.004250,
        'high': 0.004260,
        'low': 0.004230,
        'close': 0.004235  # 雖然收在 MA15 之上，但利潤回吐觸發保護
    }
    indicators = {'ma15': 0.004150}
    
    # 最高達 15%，回吐門檻 = 15% * 0.8 = 12%
    # 目前淨利 11% <= 12% -> 觸發回吐鎖利
    can_exit, reason = service.should_exit(
        side='LONG',
        bar=bar_holding,
        indicators=indicators,
        current_net_pnl=0.11,
        max_net_pnl=0.15
    )
    
    assert can_exit is True
    assert reason == "EXIT_AUTHORIZED_PROFIT_LOCK"


# ==============================================================================
# 測試場景 5：利潤曾達 12% 但回吐未達 20% —— 繼續死抱不平倉！
# ==============================================================================
def test_profit_lock_not_triggered_yet(service):
    """最高 12%，回吐門檻為 9.6%，目前利潤 10.5%，繼續護航死抱"""
    bar_holding = {
        'open': 0.004240,
        'high': 0.004250,
        'low': 0.004230,
        'close': 0.004235
    }
    indicators = {'ma15': 0.004150}
    
    can_exit, reason = service.should_exit(
        side='LONG',
        bar=bar_holding,
        indicators=indicators,
        current_net_pnl=0.105,
        max_net_pnl=0.12
    )
    
    assert can_exit is False
    assert "VETO" in reason


# ==============================================================================
# 測試場景 6：真實 StrictEntryAndImmunityGate 核心驗證
# ==============================================================================
def test_strict_entry_and_immunity_gate_can_exit():
    gate = StrictEntryAndImmunityGate()
    # 1. 多單在 MA15 之上，未達 10% 淨利，常規平倉 100% 駁回
    closed_bar = {'close': 0.004171, 'ma15': 0.004165}
    can_exit, reason = gate.can_exit('LONG', closed_bar, net_profit_pct=0.04)
    assert can_exit is False
    assert "TREND_INTACT_ABOVE_MA15 (VETO)" in reason

    # 2. 多單實體摜破 MA15，放行
    closed_bar_broken = {'close': 0.004160, 'ma15': 0.004165}
    can_exit, reason = gate.can_exit('LONG', closed_bar_broken, net_profit_pct=0.02)
    assert can_exit is True
    assert "TREND_BROKEN_BELOW_MA15" in reason

    # 3. 空單在 MA15 之下，常規平倉 100% 駁回
    closed_bar_short = {'close': 0.0430, 'ma15': 0.0440}
    can_exit, reason = gate.can_exit('SHORT', closed_bar_short, net_profit_pct=0.06)
    assert can_exit is False
    assert "TREND_INTACT_BELOW_MA15 (VETO)" in reason

    # 4. 空單實體站上 MA15，放行
    closed_bar_short_broken = {'close': 0.0445, 'ma15': 0.0440}
    can_exit, reason = gate.can_exit('SHORT', closed_bar_short_broken, net_profit_pct=0.02)
    assert can_exit is True
    assert "TREND_BROKEN_ABOVE_MA15" in reason

    # 5. 淨利達 10% 放行鎖利
    can_exit, reason = gate.can_exit('LONG', closed_bar, net_profit_pct=0.105)
    assert can_exit is True
    assert "AUTHORIZED_NET_PROFIT_LOCK" in reason

    # 6. 硬停損放行
    can_exit, reason = gate.can_exit('LONG', closed_bar, is_hard_stop=True)
    assert can_exit is True
    assert "HARD_STOP_TRIGGERED" in reason
