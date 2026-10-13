import time
import pytest
import pandas as pd

from core.services.exits.dual_track_exit_service import (
    DualTrackExitStrategy,
    is_early_lifecycle_protected,
    is_ma5_slope_shield_active,
    is_candle_closed_ma5_break,
    detect_extreme_bar_emergency_exit,
)
from core.services.entry_contract import _post_close_long_opportunity
from core.gates.pipeline import EntryGatePipeline


def test_early_lifecycle_protection_under_90s():
    """持倉未滿 90 秒，只要未跌破 1.2 ATR，強制保護不平倉。"""
    now = time.time()
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': 0.0430,
        'open_timestamp': now - 45.0,  # 45 秒前開倉
    }
    atr = 0.0002
    # 價格微跌 0.5 ATR (0.0001) 至 0.0429
    assert is_early_lifecycle_protected(position, 0.0429, atr) is True

    # 價格暴跌 1.5 ATR (0.0003) 至 0.0427 -> 擊穿 1.2 ATR，解除保護允許硬停損
    assert is_early_lifecycle_protected(position, 0.0427, atr) is False

    # 超過 90 秒 -> 解除早期保護
    position['open_timestamp'] = now - 95.0
    assert is_early_lifecycle_protected(position, 0.0429, atr) is False


def test_ma5_slope_shield():
    """強趨勢斜率盾牌：多單 MA5 斜率 > 0.03 ATR 向上，強制禁止平倉。"""
    # 測試從使用者要求的 core.services.dual_track_exit_service 直接匯入
    from core.services.dual_track_exit_service import is_ma5_slope_shield_active as alias_shield
    atr = 0.0002

    # MA5 升 0.000007 (+0.035 ATR > 0.03 ATR) -> 觸發保護
    strong_up_frame = pd.DataFrame([
        {'ma5': 0.042993},
        {'ma5': 0.043000},
    ])
    assert alias_shield(strong_up_frame, 'LONG', atr) is True

    # MA5 升 0.000004 (+0.02 ATR <= 0.03 ATR) -> 未達 0.03 ATR 門檻，不觸發盾牌
    mild_up_frame = pd.DataFrame([
        {'ma5': 0.042996},
        {'ma5': 0.043000},
    ])
    assert alias_shield(mild_up_frame, 'LONG', atr) is False

    # 平緩或走平 (斜率 0)
    flat_frame = pd.DataFrame([
        {'ma5': 0.0430},
        {'ma5': 0.0430},
    ])
    assert alias_shield(flat_frame, 'LONG', atr) is False


def test_candle_closed_only_ma5_break():
    """平倉必須收線確認：盤中跳價不平，收盤確認且跌破 MA5 才離場。"""
    frame = pd.DataFrame([
        {'close': 0.0428, 'ma5': 0.0430},
    ])
    # 盤中未收線 (is_closed=False) -> 不平倉
    assert is_candle_closed_ma5_break(frame, 'LONG', is_closed=False) is False

    # 收線確認 (is_closed=True) 且 close < ma5 -> 離場
    assert is_candle_closed_ma5_break(frame, 'LONG', is_closed=True) is True


def test_extreme_bar_emergency_exit():
    """極端大陰棒反殺緊急避險：實體 >= 0.8 ATR 且摜破 MA5 允許即時砍倉。"""
    atr = 0.0002
    # 陰棒 open 0.0430, quote 0.0428 (實體 0.0002 = 1.0 ATR >= 0.8 ATR), ma5 0.0429
    frame = pd.DataFrame([{
        'open': 0.0430,
        'high': 0.0430,
        'low': 0.0427,
        'close': 0.0428,
        'ma5': 0.0429,
    }])
    assert detect_extreme_bar_emergency_exit(frame, 0.0428, 'LONG', atr) is True

    # 普通小陰棒 (實體 0.00005 = 0.25 ATR < 0.8 ATR) -> 不砍倉
    frame_small = pd.DataFrame([{
        'open': 0.0430,
        'high': 0.0430,
        'low': 0.0429,
        'close': 0.04295,
        'ma5': 0.04298,
    }])
    assert detect_extreme_bar_emergency_exit(frame_small, 0.04295, 'LONG', atr) is False


def test_reentry_single_bar_pullback_ma5_support():
    """多單平倉後，回踩 MA5 獲得支撐並收陽線，成功觸發順勢延續開倉。"""
    close_trade_id = 1791862000000.0  # 平倉時間
    # closed bars with >= 3 bars
    closed = pd.DataFrame([
        {
            'timestamp': 1791861940000.0,
            'open': 0.0420, 'high': 0.0426, 'low': 0.0419, 'close': 0.0425,
            'ma5': 0.0423, 'ma15': 0.0418, 'atr': 0.0002,
        },
        {
            'timestamp': 1791862000000.0,
            'open': 0.0425, 'high': 0.0430, 'low': 0.0424, 'close': 0.0428,
            'ma5': 0.0426, 'ma15': 0.0420, 'atr': 0.0002,
        },
        {
            'timestamp': 1791862060000.0,
            'open': 0.0427, 'high': 0.0432, 'low': 0.0426, 'close': 0.0431,  # 回踩 0.0426 (<= ma5 0.0427) 收陽 0.0431
            'ma5': 0.0427, 'ma15': 0.0421, 'atr': 0.0002,
        },
    ])
    opportunity = _post_close_long_opportunity(closed, close_trade_id)
    assert opportunity is not None
    assert opportunity['kind'] == 'PULLBACK_SUPPORT'


def test_momentum_burst_short_blocked_when_kc_up_and_above_mid():
    """當 KC 為向上環境且價格在中軌之上時，禁止逆勢快車道開空。"""
    # 建立 11 根 K 線，KC 中軌向上，價格在 KC 中軌之上
    rows = []
    for i in range(11):
        c = 0.0950 + i * 0.0002
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': c - 0.0001,
            'high': c + 0.0003,
            'low': c - 0.0003,
            'close': c,
            'kc_upper': 0.0965,
            'kc_middle': 0.0945,  # KC 中軌
            'kc_lower': 0.0925,
            'channel_state': 'KC向上',
            'ma5': c - 0.0001,
            'ma15': c - 0.0003,
            'atr': 0.0005,
            'is_closed': i < 10,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    # 報價 0.0948 在中軌 0.0945 之上，嘗試開空
    burst = EntryGatePipeline.detect_momentum_burst(df, 0.0948, side='SHORT')
    assert burst is None


def test_momentum_burst_short_blocked_when_ma15_sloping_up():
    """當 MA15 仍維持向上斜率時，禁止執行空單快車道進場。"""
    rows = []
    for i in range(11):
        c = 0.0940 - i * 0.0001
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': c + 0.0002,
            'high': c + 0.0003,
            'low': c - 0.0003,
            'close': c,
            'kc_upper': 0.0965,
            'kc_middle': 0.0945,
            'kc_lower': 0.0925,
            'channel_state': 'KC走平',
            'ma5': 0.0940,
            'ma15': 0.0930 + i * 0.0001,  # MA15 向上斜率 (0.0930 -> 0.0940)
            'atr': 0.0005,
            'is_closed': i < 10,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    burst = EntryGatePipeline.detect_momentum_burst(df, 0.0935, side='SHORT')
    assert burst is None


def test_holding_protection_veto_by_ma5_slope_shield():
    """HoldingProtectionExitGate 驗證：若 MA5 斜率強勢順向，一票否決回踩平多。"""
    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    position = {'symbol': '龙虾/USDT', 'side': 'LONG', 'entry_price': 0.0420}
    atr = 0.0002
    frame = pd.DataFrame([
        {'ma5': 0.0428, 'atr': atr, 'kc_middle': 0.0435, 'close': 0.0428},
        {'ma5': 0.0430, 'atr': atr, 'kc_middle': 0.0435, 'close': 0.0429},  # ma5 斜率 +0.0002 = 1.0 ATR > 0.03 ATR
    ])
    is_allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, quote=0.0429, candidate_reason='EXIT_LONG_ON_SELLING_PRESSURE',
    )
    assert is_allowed is False
    assert reason == "VETO_MA5_SLOPE_SHIELD_ACTIVE"


def test_fresh_death_cross_gap_exemption():
    """初生死叉（MA5 剛下穿 MA15 且斜率向下）時，Gap 豁免，不報 BLOCKED_MA5_MA15_GAP_TOO_NARROW。"""
    from core.gates.pipeline import ma5_ma15_cross_chop_problem
    rows = []
    for i in range(15):
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': 0.0500, 'high': 0.0505, 'low': 0.0495, 'close': 0.0500,
            'ma5': 0.0502 if i < 14 else 0.0498,  # 前根 0.0502 >= ma15 0.0500，最新根 0.0498 < ma15 0.0499 (死叉向下)
            'ma15': 0.0500 if i < 14 else 0.0499,
            'is_closed': i < 14,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    problem = ma5_ma15_cross_chop_problem(df, quote=0.0498, side='SHORT')
    assert problem != 'BLOCKED_MA5_MA15_GAP_TOO_NARROW'


def test_short_kc_live_body_breakout_space_exemption():
    """當實體陰線跌破 KC 下軌且 KC_TREND_DOWN 時，豁免 25% 下軌空間限制。"""
    from core.services.entry_contract import evaluate_entry_contract
    # 建立 20 根 K 線，通道向下
    rows = []
    for i in range(21):
        mid = 0.0500 - i * 0.0002
        c = mid - 0.0005 if i == 20 else mid
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': c + 0.0003,
            'high': c + 0.0004,
            'low': c - 0.0001,
            'close': c,
            'kc_upper': mid + 0.0005,
            'kc_middle': mid,
            'kc_lower': mid - 0.0005,
            'channel_state': 'KC向下',
            'ma3': c - 0.0001,
            'ma5': c + 0.0001,
            'ma15': mid + 0.0001,
            'atr': 0.0004,
            'is_closed': i < 20,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    # 報價 0.0450 跌破 kc_lower (約 0.0455)，實體跌破下軌
    diagnostics = {}
    entry = evaluate_entry_contract(df, 0.0450, code='KC_LIVE_BODY_BREAKOUT_SHORT', diagnostics=diagnostics)
    # 驗證未被 BLOCKED_STRICT_MA_SPACING 或 BLOCKED_STRICT_CHANNEL_CONVERGENCE 攔截
    assert diagnostics.get('reason') not in ('BLOCKED_STRICT_MA_SPACING', 'BLOCKED_STRICT_CHANNEL_CONVERGENCE')



