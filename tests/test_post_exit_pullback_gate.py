import pytest
import pandas as pd
import time
from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate


def test_short_reentry_blocked_on_floor_without_pullback():
    """平空後若無任何小反彈（未摸 MA5、未反彈 0.2 ATR、價格趴在地板），嚴格一票否決！"""
    now_ms = time.time() * 1000
    exit_ts = now_ms - 60_000  # 1 分鐘前平空
    exit_price = 0.00402540
    current_quote = 0.00402500  # 趴在平倉價下方
    atr = 0.00010000
    ma5 = 0.00403500  # MA5 在上方

    # df_5m 的 high 始終低於 exit_price + 0.20 * atr (0.00404540) 且未碰 ma5
    df_5m = pd.DataFrame([
        {'timestamp': exit_ts - 300_000, 'open': 0.004050, 'high': 0.004060, 'low': 0.004020, 'close': 0.004025, 'ma5': ma5, 'atr': atr},
        {'timestamp': exit_ts, 'open': 0.004025, 'high': 0.004026, 'low': 0.004024, 'close': 0.004025, 'ma5': ma5, 'atr': atr},
        {'timestamp': now_ms, 'open': 0.004025, 'high': 0.004026, 'low': 0.004024, 'close': current_quote, 'ma5': ma5, 'atr': atr},
    ])

    passed, reason = MemeSharedProtectionGate.validate_pullback_reentry(
        df=df_5m,
        side='SHORT',
        quote=current_quote,
        last_closed_action='CLOSE_SHORT',
        last_closed_price=exit_price,
        last_closed_ts=exit_ts,
        curr_ts=now_ms,
        atr=atr,
        ma5=ma5,
    )

    assert not passed
    assert 'NO_PULLBACK_AFTER_EXIT' in reason
    assert 'Floor Chase Blocked' in reason


def test_short_reentry_allowed_after_pullback_to_ma5():
    """平空後若有向上反彈測試 MA5，小反彈遇阻後允許二次賣空！"""
    now_ms = time.time() * 1000
    exit_ts = now_ms - 120_000  # 2 分鐘前平空
    exit_price = 0.00402540
    atr = 0.00010000
    ma5 = 0.00403500

    # 盤中最高價曾反彈摸到 MA5 (0.00403600)，隨後回落至 0.00403000
    current_quote = 0.00403000
    df_5m = pd.DataFrame([
        {'timestamp': exit_ts - 300_000, 'open': 0.004050, 'high': 0.004060, 'low': 0.004020, 'close': 0.004025, 'ma5': ma5, 'atr': atr},
        {'timestamp': exit_ts, 'open': 0.004025, 'high': 0.004038, 'low': 0.004024, 'close': 0.004032, 'ma5': ma5, 'atr': atr},
        {'timestamp': now_ms, 'open': 0.004032, 'high': 0.004036, 'low': 0.004028, 'close': current_quote, 'ma5': ma5, 'atr': atr},
    ])

    passed, reason = MemeSharedProtectionGate.validate_pullback_reentry(
        df=df_5m,
        side='SHORT',
        quote=current_quote,
        last_closed_action='CLOSE_SHORT',
        last_closed_price=exit_price,
        last_closed_ts=exit_ts,
        curr_ts=now_ms,
        atr=atr,
        ma5=ma5,
    )

    assert passed
    assert reason == "PASS"


def test_long_reentry_blocked_on_ceiling_without_pullback():
    """平多後若無任何小回踩（未回踩 MA5、未回調 0.2 ATR、價格頂在天花板），嚴格一票否決！"""
    now_ms = time.time() * 1000
    exit_ts = now_ms - 60_000
    exit_price = 100.0
    current_quote = 100.5
    atr = 2.0
    ma5 = 98.0

    df_5m = pd.DataFrame([
        {'timestamp': exit_ts, 'open': 99.0, 'high': 100.5, 'low': 99.8, 'close': 100.0, 'ma5': ma5, 'atr': atr},
        {'timestamp': now_ms, 'open': 100.0, 'high': 100.6, 'low': 100.1, 'close': current_quote, 'ma5': ma5, 'atr': atr},
    ])

    passed, reason = MemeSharedProtectionGate.validate_pullback_reentry(
        df=df_5m,
        side='LONG',
        quote=current_quote,
        last_closed_action='CLOSE_LONG',
        last_closed_price=exit_price,
        last_closed_ts=exit_ts,
        curr_ts=now_ms,
        atr=atr,
        ma5=ma5,
    )

    assert not passed
    assert 'NO_PULLBACK_AFTER_EXIT' in reason
    assert 'Ceiling Chase Blocked' in reason


def test_long_reentry_allowed_after_pullback_to_ma5():
    """平多後若有向下小回踩測試 MA5 支撐，獲得支撐後允許二次開多！"""
    now_ms = time.time() * 1000
    exit_ts = now_ms - 120_000
    exit_price = 100.0
    atr = 2.0
    ma5 = 98.0
    current_quote = 98.5

    df_5m = pd.DataFrame([
        {'timestamp': exit_ts, 'open': 99.0, 'high': 100.5, 'low': 98.0, 'close': 100.0, 'ma5': ma5, 'atr': atr},
        {'timestamp': now_ms, 'open': 100.0, 'high': 100.0, 'low': 97.9, 'close': current_quote, 'ma5': ma5, 'atr': atr},
    ])

    passed, reason = MemeSharedProtectionGate.validate_pullback_reentry(
        df=df_5m,
        side='LONG',
        quote=current_quote,
        last_closed_action='CLOSE_LONG',
        last_closed_price=exit_price,
        last_closed_ts=exit_ts,
        curr_ts=now_ms,
        atr=atr,
        ma5=ma5,
    )

    assert passed
    assert reason == "PASS"


def test_pullback_filter_expires_after_30_minutes():
    """超過 30 分鐘市場結構已重置，不再受平倉回抽限制！"""
    now_ms = time.time() * 1000
    exit_ts = now_ms - 2_000_000  # 超過 30 分鐘
    exit_price = 0.00402540
    current_quote = 0.00402500
    atr = 0.00010000
    ma5 = 0.00403500

    df_5m = pd.DataFrame([
        {'timestamp': now_ms, 'open': 0.004025, 'high': 0.004026, 'low': 0.004024, 'close': current_quote, 'ma5': ma5, 'atr': atr},
    ])

    passed, reason = MemeSharedProtectionGate.validate_pullback_reentry(
        df=df_5m,
        side='SHORT',
        quote=current_quote,
        last_closed_action='CLOSE_SHORT',
        last_closed_price=exit_price,
        last_closed_ts=exit_ts,
        curr_ts=now_ms,
        atr=atr,
        ma5=ma5,
    )

    assert passed
    assert "EXCEEDS_REENTRY_WINDOW" in reason


def test_opposite_direction_not_blocked():
    """平空後若市場出現多頭大反轉，做多不應被平空回抽限制擋下！"""
    now_ms = time.time() * 1000
    exit_ts = now_ms - 60_000
    exit_price = 0.00402540
    current_quote = 0.00403000
    atr = 0.00010000
    ma5 = 0.00402800

    df_5m = pd.DataFrame([
        {'timestamp': now_ms, 'open': 0.004025, 'high': 0.004035, 'low': 0.004025, 'close': current_quote, 'ma5': ma5, 'atr': atr},
    ])

    passed, reason = MemeSharedProtectionGate.validate_pullback_reentry(
        df=df_5m,
        side='LONG',
        quote=current_quote,
        last_closed_action='CLOSE_SHORT',  # 上次是平空，這次做多
        last_closed_price=exit_price,
        last_closed_ts=exit_ts,
        curr_ts=now_ms,
        atr=atr,
        ma5=ma5,
    )

    assert passed
    assert reason == "PASS"
