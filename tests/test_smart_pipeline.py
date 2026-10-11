"""Tests for Smart Pipeline: Spatial Brain, Decoupled Gates, and Peak/Valley Exits."""
import pytest
import pandas as pd
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from core.intelligence.spatial_brain import SpatialBrain
from core.gates.pipeline import pipeline
from core.exits.peak_valley_exit import PeakValleyExit
from core.engine import TradingEngine


def test_0716_weak_body_does_not_create_realtime_breakout():
    """A weak live body cannot create an authorized pipeline breakout."""
    atr = 1.0
    base_price = 100.0
    bars = []
    for i in range(5):
        bars.append({
            'timestamp': 1700000000000 + i * 60000,
            'open': base_price,
            'high': base_price + 0.8,
            'low': base_price - 0.8,
            'close': base_price,
            'atr': atr,
            'kc_middle': base_price,
            'kc_upper': base_price + 2.0,
            'kc_lower': base_price - 2.0,
            'ma5': base_price - 0.2,
            'ma15': base_price,
            'is_closed': True,
        })
    # 07:16 微弱小陰線：Open = 97.9, Close = 97.75 (實體 0.15 < 0.35 * ATR)
    # High = 98.2, Low = 97.6 (長度 0.6, 實體佔比 0.15 / 0.6 = 25% < 55%)
    live_open = 97.9
    live_close = 97.75
    live_high = 98.2
    live_low = 97.6
    bars.append({
        'timestamp': 1700000000000 + 5 * 60000,
        'open': live_open,
        'high': live_high,
        'low': live_low,
        'close': live_close,
        'atr': atr,
        'kc_middle': base_price,
        'kc_upper': base_price + 2.0,
        'kc_lower': 98.0,
        'ma5': base_price - 0.5,
        'ma15': base_price,
        'is_closed': False,
    })
    df = pd.DataFrame(bars)
    df.attrs['timeframe_ms'] = 60000

    assert pipeline.detect_realtime_breakout(df, live_close, side='SHORT') is None
    diagnostics = {}
    auth_result = pipeline.authorize(
        {'side': 'SHORT', 'score': 100, 'entry_mode': 'CHANNEL_SWING'},
        df, live_close, symbol='LOBSTER/USDT', diagnostics=diagnostics,
    )
    assert auth_result is None
    assert diagnostics.get('reason') == 'BLOCKED_UNRECOGNIZED_PIPELINE_AUTHORITY'


def test_0459_short_pullback_triggers_fractal_valley():
    """2. 傳入 04:59 暴跌後回抽中點數據，斷言毫秒級觸發 EXIT_SHORT_ON_FRACTAL_VALLEY。"""
    # 建立 3 根 K 棒：前前棒、04:58 暴跌谷底棒、04:59 回踩棒
    atr = 1.0
    bars = [
        # B1: 04:57
        {'timestamp': 1000, 'open': 102.0, 'high': 102.5, 'low': 101.5, 'close': 101.8, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 101.0, 'ma15': 101.5},
        # B2: 04:58 暴跌至谷底 (Low = 95.0, 最低點形成分形谷底)
        {'timestamp': 2000, 'open': 101.8, 'high': 102.0, 'low': 95.0, 'close': 96.0, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 99.0, 'ma15': 101.0},
        # B3: 04:59 strong bullish body confirms above the prior midpoint, MA5, and KC middle.
        {'timestamp': 3000, 'open': 96.0, 'high': 99.2, 'low': 95.8, 'close': 99.2, 'is_closed': True, 'atr': atr, 'kc_middle': 98.8, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 98.6, 'ma15': 100.5},
    ]
    df = pd.DataFrame(bars)

    # 前棒 B2: Open = 101.8, Close = 96.0 -> Midpoint = (101.8 + 96.0) / 2 = 98.9
    # 即時價格 quote = 99.0 (> 98.9 突破前棒實體中點)
    quote = 99.0
    position = {'side': 'SHORT', 'entry_price': 101.5, 'symbol': 'CAP/USDT'}

    exit_reason, exit_info = PeakValleyExit.evaluate(position, df, quote)
    assert exit_reason == 'EXIT_SHORT_ON_FRACTAL_VALLEY'
    assert exit_info.get('fractal_bottom') is True
    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    held_exit, _ = HoldingProtectionExitGate.evaluate(position, df, quote)
    assert held_exit == 'EXIT_BY_VERIFIED_FRACTAL_VALLEY'
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, df, quote, exit_reason, exit_info,
    )
    assert allowed is True


def test_short_intrabar_small_green_bounce_waits_for_bar_close():
    from core.gates.holding_protection_gate import HoldingProtectionExitGate

    frame = pd.DataFrame([
        {'timestamp': 1000, 'open': 100.0, 'high': 100.3, 'low': 99.5, 'close': 99.8, 'atr': 1.0, 'kc_middle': 100.0, 'ma5': 99.8, 'is_closed': True},
        {'timestamp': 2000, 'open': 99.8, 'high': 100.0, 'low': 98.5, 'close': 98.7, 'atr': 1.0, 'kc_middle': 99.8, 'ma5': 99.4, 'is_closed': True},
        {'timestamp': 3000, 'open': 97.4, 'high': 98.1, 'low': 97.3, 'close': 97.6, 'atr': 1.0, 'kc_middle': 98.0, 'ma5': 97.8, 'is_closed': False},
    ])
    position = {
        'side': 'SHORT', 'entry_price': 100.0, 'lowest_price': 97.0,
        'symbol': 'LOBSTER/USDT',
    }

    exit_reason, _ = HoldingProtectionExitGate.evaluate(position, frame, 97.6)
    peak_valley_reason, _ = PeakValleyExit.evaluate(position, frame, 97.6)
    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, 97.6, 'EXIT_SHORT_ON_FRACTAL_VALLEY',
        details={'atr': 1.0},
    )

    assert exit_reason is None
    assert peak_valley_reason is None
    assert allowed is False
    assert reason == HoldingProtectionExitGate.WAIT_CLOSE_REJECT_REASON


def test_short_intrabar_extreme_v_reversal_can_exit():
    from core.gates.holding_protection_gate import HoldingProtectionExitGate

    frame = pd.DataFrame([
        {'timestamp': 1000, 'open': 100.0, 'high': 100.3, 'low': 99.5, 'close': 99.8, 'atr': 1.0, 'kc_middle': 99.0, 'is_closed': True},
        {'timestamp': 2000, 'open': 99.8, 'high': 100.0, 'low': 98.5, 'close': 98.7, 'atr': 1.0, 'kc_middle': 98.0, 'is_closed': True},
        {'timestamp': 3000, 'open': 97.4, 'high': 98.1, 'low': 95.0, 'close': 97.3, 'atr': 1.0, 'kc_middle': 96.0, 'is_closed': False},
    ])
    position = {
        'side': 'SHORT', 'entry_price': 100.0, 'lowest_price': 97.0,
        'symbol': 'LOBSTER/USDT',
    }

    reason, details = HoldingProtectionExitGate.evaluate(position, frame, 97.3)
    allowed, validated_reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, 97.3, 'EXIT_BY_EXTREME_WATERFALL', details,
    )

    assert reason == 'EXIT_BY_EXTREME_WATERFALL'
    assert details['bar_pump'] >= 1.2 * details['atr']
    assert allowed is True
    assert validated_reason == 'EXIT_BY_EXTREME_WATERFALL'


def test_short_closed_red_long_upper_wick_doji_does_not_exit_valley():
    from core.gates.holding_protection_gate import HoldingProtectionExitGate

    frame = pd.DataFrame([
        {'timestamp': 1000, 'open': 101.0, 'high': 101.5, 'low': 100.0, 'close': 100.5, 'atr': 1.0, 'kc_middle': 100.0, 'ma5': 100.5, 'is_closed': True},
        {'timestamp': 2000, 'open': 100.5, 'high': 101.0, 'low': 95.0, 'close': 96.0, 'atr': 1.0, 'kc_middle': 99.0, 'ma5': 98.0, 'is_closed': True},
        {'timestamp': 3000, 'open': 96.0, 'high': 97.0, 'low': 95.1, 'close': 95.9, 'atr': 1.0, 'kc_middle': 98.0, 'ma5': 96.2, 'is_closed': True},
    ])
    position = {
        'side': 'SHORT', 'entry_price': 102.0, 'lowest_price': 95.0,
        'symbol': 'LOBSTER/USDT',
    }

    exit_reason, _ = HoldingProtectionExitGate.evaluate(position, frame, 95.9)
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, 95.9, 'EXIT_SHORT_ON_FRACTAL_VALLEY',
        details={'atr': 1.0},
    )

    assert exit_reason is None
    assert allowed is False


def test_0618_long_peak_triggers_fractal_peak():
    """3. 傳入 06:18 頂部大陰線跌破前棒中點與 MA3 下勾數據，斷言毫秒級觸發 EXIT_LONG_ON_FRACTAL_PEAK。"""
    # 建立 3 根 K 棒：前前棒、06:17 衝頂棒 (High = 105.0 形成頂峰)、06:18 頂部大陰棒
    atr = 1.0
    bars = [
        # B1: 06:16
        {'timestamp': 1000, 'open': 98.0, 'high': 101.0, 'low': 97.5, 'close': 100.5, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 99.0, 'ma15': 98.0},
        # B2: 06:17 衝頂極值 (High = 105.0)
        {'timestamp': 2000, 'open': 100.5, 'high': 105.0, 'low': 100.0, 'close': 104.0, 'atr': atr, 'kc_middle': 100.5, 'kc_upper': 102.5, 'kc_lower': 98.5, 'ma5': 101.5, 'ma15': 98.5},
        # B3: 06:18 頂部高點回落 (High = 104.2 <= 105.0，確認 B2 為頂峰)
        {'timestamp': 3000, 'open': 104.0, 'high': 104.2, 'low': 101.0, 'close': 101.5, 'atr': atr, 'kc_middle': 100.8, 'kc_upper': 102.8, 'kc_lower': 98.8, 'ma5': 101.8, 'ma15': 99.0},
    ]
    df = pd.DataFrame(bars)

    # 前棒 B2: Open = 100.5, Close = 104.0 -> Midpoint = 102.25
    # 即時價格 quote = 101.8 (< 102.25 跌破前棒實體中點)
    quote = 101.8
    # 設置已衝高獲利部位 (Peak ROE = (105.0 - 99.0) / 99.0 = 6.06% >= 3%)
    position = {'side': 'LONG', 'entry_price': 99.0, 'highest_price': 105.0, 'symbol': 'LOBSTER/USDT'}

    exit_reason, exit_info = PeakValleyExit.evaluate(position, df, quote)
    assert exit_reason == 'EXIT_LONG_ON_FRACTAL_PEAK'
    assert exit_info.get('fractal_top') is True


def test_early_breathing_room_blocks_premature_peak_exit():
    """4. 剛開多單（Peak ROE 僅 0.8%），次棒出現跌破前棒中點時，斷言【不可平倉（回傳 None）】。"""
    atr = 1.0
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 100.5, 'low': 99.8, 'close': 100.4, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 100.2, 'ma15': 99.8},
        {'timestamp': 2000, 'open': 100.4, 'high': 100.8, 'low': 100.2, 'close': 100.7, 'atr': atr, 'kc_middle': 100.1, 'kc_upper': 102.1, 'kc_lower': 98.1, 'ma5': 100.4, 'ma15': 99.9},
        {'timestamp': 3000, 'open': 100.7, 'high': 100.7, 'low': 100.3, 'close': 100.4, 'atr': atr, 'kc_middle': 100.2, 'kc_upper': 102.2, 'kc_lower': 98.2, 'ma5': 100.5, 'ma15': 100.0},
    ]
    df = pd.DataFrame(bars)

    # 剛開多單：entry = 100.0, highest_price = 100.8 (Peak ROE 僅 0.8% < 3.0%, 距開倉價 0.8 < 0.5*ATR(0.5? wait: atr=1.0, 0.8 >= 0.5, let entry=100.0, atr=2.0 so 0.8 < 1.0))
    df['atr'] = 2.0
    position = {'side': 'LONG', 'entry_price': 100.0, 'highest_price': 100.8, 'symbol': 'CAP/USDT'}
    # quote = 100.4 (跌破前棒中點 100.55)
    quote = 100.4

    exit_reason, exit_info = PeakValleyExit.evaluate(position, df, quote)
    assert exit_reason is None, f"Expected None, got {exit_reason}"


def test_meaningful_profit_authorizes_peak_exit():
    """5. 開多單衝高（Peak ROE 達 4.5%），隨後跌破前棒中點時，斷言【立即觸發 EXIT_LONG_ON_FRACTAL_PEAK】。"""
    atr = 1.0
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 101.5, 'low': 99.8, 'close': 101.2, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 100.5, 'ma15': 99.5},
        {'timestamp': 2000, 'open': 101.2, 'high': 104.5, 'low': 101.0, 'close': 104.0, 'atr': atr, 'kc_middle': 100.5, 'kc_upper': 102.5, 'kc_lower': 98.5, 'ma5': 101.5, 'ma15': 99.8},
        {'timestamp': 3000, 'open': 104.0, 'high': 104.2, 'low': 102.0, 'close': 102.2, 'atr': atr, 'kc_middle': 101.0, 'kc_upper': 103.0, 'kc_lower': 99.0, 'ma5': 102.0, 'ma15': 100.0},
    ]
    df = pd.DataFrame(bars)

    # 開多衝高：entry = 100.0, highest_price = 104.5 (Peak ROE = 4.5% >= 3.0%)
    position = {'side': 'LONG', 'entry_price': 100.0, 'highest_price': 104.5, 'symbol': 'CAP/USDT'}
    # quote = 102.2 (跌破前棒中點 (101.2 + 104.0) / 2 = 102.6)
    quote = 102.2

    exit_reason, exit_info = PeakValleyExit.evaluate(position, df, quote)
    assert exit_reason == 'EXIT_LONG_ON_FRACTAL_PEAK'
    assert exit_info.get('peak_roe') >= 0.045


def test_realtime_breakout_fast_track():
    """6. 盤中即時破軌快車道：Price > KC_Upper 且實體飽滿，斷言在收線前即回傳 AUTHORIZED_REALTIME_BREAKOUT。"""
    atr = 1.0
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 100.5, 'low': 99.8, 'close': 100.4, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 101.5, 'kc_lower': 98.5, 'ma5': 100.2, 'ma15': 99.8},
        {'timestamp': 2000, 'open': 100.4, 'high': 100.8, 'low': 100.2, 'close': 100.7, 'atr': atr, 'kc_middle': 100.2, 'kc_upper': 101.6, 'kc_lower': 98.6, 'ma5': 100.4, 'ma15': 99.9},
        # 當前盤中進行中的 K 棒 (尚未收線)：open=100.7, low=100.6, high=102.5, live quote=102.4
        # KC_upper = 101.8. Quote 102.4 > 101.8
        # (Price - Open) = 102.4 - 100.7 = 1.7 >= 0.35 * 1.0
        # (Price - Open) / (Price - Low) = 1.7 / (102.4 - 100.6) = 1.7 / 1.8 = 94.4% >= 60%
        {'timestamp': 3000, 'open': 100.7, 'high': 102.5, 'low': 100.6, 'close': 102.4, 'is_closed': False, 'atr': atr, 'kc_middle': 100.5, 'kc_upper': 101.8, 'kc_lower': 98.8, 'ma5': 101.2, 'ma15': 100.0},
    ]
    df = pd.DataFrame(bars)
    live_quote = 102.4

    # 1. 驗證 detect_realtime_breakout
    rt_signal = pipeline.detect_realtime_breakout(df, live_quote)
    assert rt_signal is not None
    assert rt_signal['type'] == 'AUTHORIZED_REALTIME_BREAKOUT'
    assert rt_signal['side'] == 'LONG'
    assert rt_signal['override_cooldown'] is True

    # 2. 驗證 pipeline.authorize 授權
    diagnostics = {}
    authorized = pipeline.authorize(rt_signal, df, live_quote, symbol='LOBSTER/USDT', diagnostics=diagnostics)
    assert authorized is not None
    assert authorized['_is_authorized'] is True
    assert authorized['side'] == 'LONG'
    assert authorized['type'] == 'AUTHORIZED_REALTIME_BREAKOUT'


def test_waterfall_short_breakout_reaches_engine_submission_without_volatility_veto(monkeypatch):
    symbol = 'LOBSTER/USDT'
    stamp = int(time.time() // 60) * 60_000
    rows = [
        {
            'timestamp': stamp - (5 - index) * 60_000,
            'open': 100.0,
            'high': 100.4,
            'low': 99.6,
            'close': 100.0,
            'atr': 1.0,
            'kc_middle': 100.0,
            'kc_upper': 102.0,
            'kc_lower': 98.0,
            'ma5': 100.0,
            'ma15': 100.0,
            'is_closed': True,
        }
        for index in range(5)
    ]
    quote = 96.5
    rows.append({
        'timestamp': stamp,
        'open': 100.0,
        'high': 100.0,
        'low': quote,
        'close': quote,
        'atr': 1.0,
        'kc_middle': 100.0,
        'kc_upper': 102.0,
        'kc_lower': 98.0,
        'ma5': 100.0,
        'ma15': 100.0,
        'is_closed': False,
    })
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    frame.attrs['entry_finality_verified'] = True

    decision = pipeline.authorize(
        None, frame, quote, symbol=symbol, requested_side='SHORT',
    )
    assert decision is not None
    assert decision['type'] == 'AUTHORIZED_REALTIME_BREAKOUT'
    assert decision['side'] == 'SHORT'
    assert decision['realtime_body_atr'] >= 1.5

    account = SimpleNamespace(
        positions={},
        pending_limit_orders={},
        trades=[],
        logs=[],
        log=Mock(),
        daily_loss_limit_hit=lambda: (False, 0.0),
        get_wallet_balance=lambda: 1000.0,
        get_available_balance=lambda: 1000.0,
        open_position=AsyncMock(return_value=True),
    )
    engine = object.__new__(TradingEngine)
    engine.account = account
    engine.tickers = {symbol: quote}
    engine._channel_entry_quote_times = {symbol: time.time()}
    engine.symbol_rotation = SimpleNamespace(
        get_dynamic_leverage=lambda *_args: 2,
    )
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._fresh_channel_entry_snapshot = AsyncMock(return_value={
        'frame': frame, 'price': quote, 'decision': decision,
    })
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [symbol])
    monkeypatch.setattr('core.config.is_entry_disabled', lambda _symbol: False)
    monkeypatch.setattr(
        'core.services.entry_contract.evaluate_entry_contract',
        lambda *_args, **_kwargs: decision,
    )
    monkeypatch.setattr(
        'core.services.entry_contract.entry_direction_problem',
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        'core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',
        lambda **_kwargs: None,
    )
    signal = {
        'side': 'SHORT',
        'score': 100,
        'entry_mode': 'CHANNEL_SWING',
        'signal_code': decision['type'],
        'candidate_bar_id': decision['confirmation_bar_id'],
    }

    allowed = asyncio.run(
        engine._place_structured_entry_locked(symbol, signal, quote),
    )

    assert allowed is True
    account.open_position.assert_awaited_once()
    logged = '\n'.join(str(call.args[0]) for call in account.log.call_args_list)
    assert 'reason=ACCOUNT_SUBMIT' in logged
    assert 'BLOCKED_ABNORMAL_MARKET_ENTRY' not in logged


def test_pipeline_authorization_survives_two_second_revalidation_grace(monkeypatch):
    symbol = 'LOBSTER/USDT'
    stamp = int(time.time() // 60) * 60_000
    frame = pd.DataFrame([
        {
            'timestamp': stamp - (3 - index) * 60_000,
            'open': 100.0,
            'high': 100.4,
            'low': 99.6,
            'close': 100.0,
            'atr': 1.0,
            'kc_middle': 100.0,
            'kc_upper': 102.0,
            'kc_lower': 98.0,
            'ma5': 100.0,
            'ma15': 100.0,
            'is_closed': True,
        }
        for index in range(3)
    ] + [{
        'timestamp': stamp,
        'open': 100.0,
        'high': 100.0,
        'low': 96.5,
        'close': 96.5,
        'atr': 1.0,
        'kc_middle': 100.0,
        'kc_upper': 102.0,
        'kc_lower': 98.0,
        'ma5': 100.0,
        'ma15': 100.0,
        'is_closed': False,
    }])
    frame.attrs['timeframe_ms'] = 60_000
    frame.attrs['entry_finality_verified'] = True
    authorized = pipeline.authorize(
        None, frame, 96.5, symbol=symbol, requested_side='SHORT',
    )
    assert authorized is not None

    account = SimpleNamespace(
        positions={},
        pending_limit_orders={},
        trades=[],
        logs=[],
        log=Mock(),
        daily_loss_limit_hit=lambda: (False, 0.0),
        get_wallet_balance=lambda: 1000.0,
        get_available_balance=lambda: 1000.0,
        open_position=AsyncMock(),
        breakout_qualification={},
        record_qualification=Mock(),
        consume_breakout_qualification=Mock(),
    )
    engine = object.__new__(TradingEngine)
    engine.account = account
    engine.tickers = {symbol: 96.5}
    engine._channel_entry_quote_times = {symbol: time.time()}
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *_args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    calls = 0

    def evaluate_with_transient_revalidation_failure(*_args, diagnostics=None, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return dict(authorized)
        diagnostics['reason'] = 'WAIT_PIPELINE_TRIGGER'
        return None

    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [symbol])
    monkeypatch.setattr('core.config.is_entry_disabled', lambda _symbol: False)
    monkeypatch.setattr(
        'core.services.entry_contract.evaluate_entry_contract',
        evaluate_with_transient_revalidation_failure,
    )
    monkeypatch.setattr(
        'core.services.entry_contract.entry_direction_problem',
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        'core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',
        lambda **_kwargs: None,
    )
    monotonic_calls = 0

    def two_second_revalidation_clock():
        nonlocal monotonic_calls
        monotonic_calls += 1
        return 100.0 if monotonic_calls == 1 else 102.0

    monkeypatch.setattr('core.engine.time.monotonic', two_second_revalidation_clock)
    from core.services.entry_firewall import validate_account_entry

    async def open_with_firewall(**kwargs):
        await validate_account_entry(
            account, symbol, kwargs['side'], kwargs['entry_context'],
        )
        return True

    account.open_position.side_effect = open_with_firewall

    allowed = asyncio.run(engine._execute_confirmed_channel_break(
        symbol, frame, 96.5, 'SHORT',
        v8_reason=authorized['type'],
        candidate_bar_id=authorized['confirmation_bar_id'],
    ))

    assert allowed is True
    assert calls == 2
    account.open_position.assert_awaited_once()
    logged = '\n'.join(str(call.args[0]) for call in account.log.call_args_list)
    assert 'reason=PIPELINE_AUTH_TTL_GRACE' in logged
    assert 'reason=ACCOUNT_SUBMIT' in logged


@pytest.mark.parametrize(
    ('elapsed', 'price', 'quote_age', 'expected'),
    [
        (3.0, 100.8, 30.0, True),
        (3.001, 100.0, 0.0, False),
        (1.0, 100.8001, 0.0, False),
        (1.0, 100.0, 30.001, False),
    ],
)
def test_pipeline_authorization_grace_enforces_ttl_slippage_and_quote_freshness(
    monkeypatch, elapsed, price, quote_age, expected,
):
    from core.engine import TradingEngine

    engine = object.__new__(TradingEngine)
    engine._channel_entry_quote_times = {
        'LOBSTER/USDT': 1_000.0 - quote_age,
    }
    monkeypatch.setattr('core.engine.time.time', lambda: 1_000.0)
    monkeypatch.setattr('core.engine.time.monotonic', lambda: 100.0 + elapsed)
    signal = {
        '_pipeline_authorized_at': 100.0,
        '_pipeline_authorized_price': 100.0,
        '_pipeline_authorized_atr': 1.0,
        '_pipeline_pending_signal_id': 'signal-1',
        'candidate_bar_id': 1_000.0,
        '_pipeline_authorized_decision': {
            '_is_authorized': True,
            'type': 'AUTHORIZED_BY_TREND_CONTINUATION_SHORT',
            'side': 'SHORT',
            'pending_signal_id': 'signal-1',
            'confirmation_bar_id': 1_000.0,
            'entry_atr': 1.0,
        },
    }

    decision = engine._pipeline_authorization_grace_decision(
        signal, 'LOBSTER/USDT', 'SHORT', price,
    )

    assert (decision is not None) is expected


def test_realtime_breakout_uses_full_candle_solidity_and_3_5_atr_limit():
    rows = [
        {'timestamp': 1000, 'open': 100.0, 'high': 100.5, 'low': 99.8, 'close': 100.4, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 101.5, 'kc_lower': 98.5, 'ma5': 100.2, 'ma15': 99.8},
        {'timestamp': 2000, 'open': 100.4, 'high': 100.8, 'low': 100.2, 'close': 100.7, 'atr': 1.0, 'kc_middle': 100.2, 'kc_upper': 101.6, 'kc_lower': 98.6, 'ma5': 100.4, 'ma15': 99.9},
        {'timestamp': 3000, 'open': 100.7, 'high': 105.3, 'low': 100.6, 'close': 105.3, 'is_closed': False, 'atr': 1.0, 'kc_middle': 100.5, 'kc_upper': 101.8, 'kc_lower': 98.8, 'ma5': 101.2, 'ma15': 100.0},
    ]
    frame = pd.DataFrame(rows)

    at_limit = pipeline.detect_realtime_breakout(frame, 105.3, side='LONG')
    beyond_limit = pipeline.detect_realtime_breakout(frame, 105.3001, side='LONG')

    assert at_limit is not None
    assert at_limit['realtime_distance_atr'] == pytest.approx(3.5)
    assert beyond_limit is None

    frame.loc[2, 'high'] = 104.0
    wick_dominated = pipeline.detect_realtime_breakout(frame, 102.4, side='LONG')
    assert wick_dominated is None


def test_trend_continuation_without_kc_breakout():
    """7. 順勢延續開倉（通道 B）：多頭排列（MA5 > MA15，斜率向上），K 棒收在 KC 上軌以內，但 Close > MA5 為飽滿陽線，斷言 100% 成功授權 AUTHORIZED_BY_TREND_CONTINUATION_LONG。"""
    atr = 1.0
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 101.0, 'low': 99.8, 'close': 100.8, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 103.5, 'kc_lower': 96.5, 'ma5': 100.4, 'ma15': 99.5},
        {'timestamp': 2000, 'open': 100.8, 'high': 101.5, 'low': 100.5, 'close': 101.3, 'atr': atr, 'kc_middle': 100.3, 'kc_upper': 103.8, 'kc_lower': 96.8, 'ma5': 100.8, 'ma15': 99.8},
        # 當前棒：open=101.2, high=102.6, low=101.0, live close/quote=102.5.
        # KC 上軌為 104.0 (Quote 102.5 遠在 KC 上軌 104.0 以內，完全未破外軌！)
        # MA5 = 101.6 > MA15 = 100.2, MA15 斜率向上 (100.2 >= 99.8), KC 中軌向上 (100.6 >= 100.3)
        # Close (102.5) > MA5 (101.6) 且 Close (102.5) > Open (101.2) 為飽滿陽線
        # 實體長度 = 1.3, 震幅 = 102.6 - 101.0 = 1.6, 實體佔比 = 1.3 / 1.6 = 81.25% >= 50%
        {'timestamp': 3000, 'open': 101.2, 'high': 102.6, 'low': 101.0, 'close': 102.5, 'is_closed': False, 'atr': atr, 'kc_middle': 100.6, 'kc_upper': 104.0, 'kc_lower': 97.2, 'ma5': 101.6, 'ma15': 100.2},
    ]
    df = pd.DataFrame(bars)
    live_quote = 102.5

    # 1. 驗證 detect_trend_continuation 成功偵測
    cont_signal = pipeline.detect_trend_continuation(df, live_quote)
    assert cont_signal is not None
    assert cont_signal['type'] == 'AUTHORIZED_BY_TREND_CONTINUATION_LONG'
    assert cont_signal['side'] == 'LONG'
    assert cont_signal['is_trend_continuation'] is True

    # 2. 驗證 pipeline.authorize 通過全部 Gate 授權
    diagnostics = {}
    authorized = pipeline.authorize(cont_signal, df, live_quote, symbol='CAP/USDT', diagnostics=diagnostics)
    assert authorized is not None, f"Expected authorized, got diagnostics: {diagnostics}"
    assert authorized['_is_authorized'] is True
    assert authorized['side'] == 'LONG'
    assert authorized['type'] == 'AUTHORIZED_BY_TREND_CONTINUATION_LONG'


def test_trend_continuation_uses_strict_ma15_slope_without_atr_body_floor():
    rows = [
        {'timestamp': 1000, 'open': 100.0, 'high': 100.1, 'low': 99.9, 'close': 100.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 103.0, 'kc_lower': 97.0, 'ma5': 99.9, 'ma15': 99.8},
        {'timestamp': 2000, 'open': 100.0, 'high': 100.1, 'low': 99.9, 'close': 100.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 103.0, 'kc_lower': 97.0, 'ma5': 100.0, 'ma15': 99.9},
        {'timestamp': 3000, 'open': 100.0, 'high': 100.2, 'low': 100.0, 'close': 100.11, 'is_closed': False, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 103.0, 'kc_lower': 97.0, 'ma5': 100.05, 'ma15': 100.0},
    ]
    frame = pd.DataFrame(rows)

    decision = pipeline.authorize(None, frame, 100.11, requested_side='LONG')
    assert decision is not None
    assert decision['type'] == 'AUTHORIZED_BY_TREND_CONTINUATION_LONG'

    frame.loc[2, 'ma15'] = frame.loc[1, 'ma15']
    assert pipeline.detect_trend_continuation(frame, 100.11, side='LONG') is None


def test_trend_continuation_without_breaking_rails():
    """8. 測試順勢延續開倉（無需破外軌）：test_trend_continuation_without_breaking_rails()。"""
    atr = 1.0
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 101.0, 'low': 99.8, 'close': 100.8, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 105.0, 'kc_lower': 95.0, 'ma5': 100.4, 'ma15': 99.5},
        {'timestamp': 2000, 'open': 100.8, 'high': 101.5, 'low': 100.5, 'close': 101.3, 'atr': atr, 'kc_middle': 100.3, 'kc_upper': 105.0, 'kc_lower': 95.0, 'ma5': 100.8, 'ma15': 99.8},
        # 在通道內收出 Close > MA5 的實體大陽線 (Quote 102.5 遠小於 KC_Upper 105.0)
        {'timestamp': 3000, 'open': 101.2, 'high': 102.6, 'low': 101.0, 'close': 102.5, 'is_closed': False, 'atr': atr, 'kc_middle': 100.6, 'kc_upper': 105.0, 'kc_lower': 95.0, 'ma5': 101.6, 'ma15': 100.2},
    ]
    df = pd.DataFrame(bars)
    live_quote = 102.5

    cont_signal = pipeline.detect_trend_continuation(df, live_quote)
    assert cont_signal is not None
    assert cont_signal['type'] == 'AUTHORIZED_BY_TREND_CONTINUATION_LONG'
    assert cont_signal['side'] == 'LONG'

    authorized = pipeline.authorize(cont_signal, df, live_quote, symbol='CAP/USDT')
    assert authorized is not None
    assert authorized['_is_authorized'] is True
    assert authorized['type'] == 'AUTHORIZED_BY_TREND_CONTINUATION_LONG'


def test_peak_reversal_flip_to_short():
    """9. 測試頂底轉折次棒大實體反向開倉（Reversal Flip）：test_peak_reversal_flip_to_short()。
    高位平多後，次棒出現飽滿大陰線 (實體 >= 60% 且 >= 0.35*ATR) 摜破 MA5 與中軌，立即授權 AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT。
    """
    atr = 1.0
    bars = [
        # 前棒衝高至 105.0，高位收 104.5，MA5=103.5，KC_Middle=102.0
        {'timestamp': 1000, 'open': 101.0, 'high': 103.0, 'low': 100.8, 'close': 102.8, 'atr': atr, 'kc_middle': 101.5, 'kc_upper': 106.0, 'kc_lower': 97.0, 'ma5': 102.0, 'ma15': 100.0},
        {'timestamp': 2000, 'open': 102.8, 'high': 105.0, 'low': 102.5, 'close': 104.5, 'atr': atr, 'kc_middle': 102.0, 'kc_upper': 106.5, 'kc_lower': 97.5, 'ma5': 103.5, 'ma15': 100.5},
        # 次棒走出飽滿大陰線摜破 MA5 (102.8) 與中軌 (102.0)：open=104.5, high=104.6, low=101.5, live quote=101.8
        # 實體長度 = 104.5 - 101.8 = 2.7 >= 0.35 * 1.0
        # 震幅 = 104.6 - 101.5 = 3.1
        # 實體佔比 = 2.7 / 3.1 = 87.1% >= 60%
        {'timestamp': 3000, 'open': 104.5, 'high': 104.6, 'low': 101.5, 'close': 101.8, 'is_closed': False, 'atr': atr, 'kc_middle': 102.0, 'kc_upper': 106.5, 'kc_lower': 97.5, 'ma5': 102.8, 'ma15': 100.8},
    ]
    df = pd.DataFrame(bars)
    live_quote = 101.8

    # 1. 驗證 detect_reversal_flip 成功識別頂底翻轉
    flip_signal = pipeline.detect_reversal_flip(df, live_quote)
    assert flip_signal is not None
    assert flip_signal['type'] == 'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT'
    assert flip_signal['side'] == 'SHORT'
    assert flip_signal.get('override_cooldown') is not True

    # 2. 驗證 pipeline.authorize 成功授權 (豁免逆勢排列檢查)
    diagnostics = {}
    authorized = pipeline.authorize(flip_signal, df, live_quote, symbol='CAP/USDT', diagnostics=diagnostics)
    assert authorized is not None, f"Expected authorized, got diagnostics: {diagnostics}"
    assert authorized['_is_authorized'] is True
    assert authorized['side'] == 'SHORT'
    assert authorized['type'] == 'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT'


def test_short_upper_wick_bearish_candle_does_not_exit():
    """A rejected upper wick with a bearish body must not close a profitable short."""
    bars = [
        {'timestamp': 1000, 'open': 96.0, 'high': 98.0, 'low': 96.0, 'close': 95.8, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 96.0, 'ma15': 97.0},
        {'timestamp': 2000, 'open': 97.0, 'high': 98.0, 'low': 95.0, 'close': 96.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 96.5, 'ma15': 97.0},
        {'timestamp': 3000, 'open': 97.0, 'high': 99.0, 'low': 95.8, 'close': 96.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 96.8, 'ma15': 97.0},
    ]
    position = {'side': 'SHORT', 'entry_price': 101.5}

    exit_reason, _ = PeakValleyExit.evaluate(position, pd.DataFrame(bars), quote=96.5)

    assert exit_reason is None


def test_short_upper_wick_at_04_atr_suppresses_all_peak_valley_exits():
    bars = [
        {'timestamp': 1000, 'open': 97.0, 'high': 97.2, 'low': 96.5, 'close': 96.8, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 97.0, 'ma15': 97.5},
        {'timestamp': 2000, 'open': 97.0, 'high': 97.2, 'low': 96.0, 'close': 96.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 96.8, 'ma15': 97.3},
        {'timestamp': 3000, 'open': 96.0, 'high': 96.4, 'low': 95.0, 'close': 95.9, 'is_closed': True, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 96.5, 'ma15': 97.0},
    ]
    position = {'side': 'SHORT', 'entry_price': 101.5}

    exit_reason, exit_info = PeakValleyExit.evaluate(
        position, pd.DataFrame(bars), quote=95.9,
    )

    assert exit_reason is None
    assert exit_info['upper_wick_hold'] is True


def test_short_fractal_upper_wick_cannot_confirm_valley_exit():
    from core.gates.holding_protection_gate import HoldingProtectionExitGate

    bars = [
        {'timestamp': 1000, 'open': 102.0, 'high': 102.5, 'low': 101.5, 'close': 101.8, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 101.0},
        {'timestamp': 2000, 'open': 101.8, 'high': 102.0, 'low': 95.0, 'close': 96.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 99.0},
        {'timestamp': 3000, 'open': 96.0, 'high': 100.0, 'low': 95.8, 'close': 96.1, 'atr': 1.0, 'kc_middle': 98.8, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 98.6, 'is_closed': False},
    ]
    position = {'side': 'SHORT', 'entry_price': 101.5}

    exit_reason, details = HoldingProtectionExitGate.evaluate(
        position, pd.DataFrame(bars), quote=96.1,
    )

    assert exit_reason is None
    assert details['upper_wick_hold'] is True


def test_short_lower_wick_rejection_exits_profitable_short():
    """A long lower wick after a meaningful short profit triggers the short-specific exit."""
    bars = [
        {'timestamp': 1000, 'open': 97.0, 'high': 97.2, 'low': 96.5, 'close': 96.8, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 97.0, 'ma15': 97.5},
        {'timestamp': 2000, 'open': 97.0, 'high': 97.2, 'low': 96.0, 'close': 96.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 96.8, 'ma15': 97.3},
        {'timestamp': 3000, 'open': 96.0, 'high': 96.6, 'low': 94.8, 'close': 96.5, 'is_closed': True, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 96.4, 'ma15': 97.0},
    ]
    position = {'side': 'SHORT', 'entry_price': 101.5}

    exit_reason, exit_info = PeakValleyExit.evaluate(position, pd.DataFrame(bars), quote=96.1)

    assert exit_reason == 'EXIT_SHORT_ON_LOWER_WICK_REJECTION'
    assert exit_info['lower_wick'] >= 0.5 * exit_info['atr']
    assert exit_info['lower_wick'] > 2.0 * exit_info['body']
    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, pd.DataFrame(bars), 96.1, exit_reason, exit_info,
    )
    assert allowed is True


def test_peak_flip_short_requires_confirmed_long_close_and_live_bearish_body():
    from types import SimpleNamespace

    t0 = 1_700_000_000_000
    rows = [
        {'timestamp': t0, 'open': 102.0, 'high': 102.2, 'low': 101.7, 'close': 101.8, 'atr': 1.0, 'kc_middle': 102.0, 'kc_upper': 104.0, 'kc_lower': 100.0, 'ma3': 102.0, 'ma5': 102.0, 'ma15': 103.0, 'is_closed': True},
        {'timestamp': t0 + 60000, 'open': 101.8, 'high': 102.0, 'low': 101.1, 'close': 101.2, 'atr': 1.0, 'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8, 'ma3': 101.8, 'ma5': 101.7, 'ma15': 102.8, 'is_closed': True},
        {'timestamp': t0 + 120000, 'open': 101.2, 'high': 101.4, 'low': 100.7, 'close': 100.8, 'atr': 1.0, 'kc_middle': 101.5, 'kc_upper': 103.5, 'kc_lower': 99.5, 'ma3': 101.5, 'ma5': 101.3, 'ma15': 102.5, 'is_closed': True},
        {'timestamp': t0 + 180000, 'open': 100.8, 'high': 100.9, 'low': 100.1, 'close': 100.8, 'atr': 1.0, 'kc_middle': 101.2, 'kc_upper': 103.2, 'kc_lower': 99.2, 'ma3': 101.2, 'ma5': 100.5, 'ma15': 102.2, 'is_closed': False},
    ]
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60000
    account = SimpleNamespace(
        positions={},
        log=lambda *args: None,
        trades=[{
            'id': t0 + 180000 + 1000,
            'symbol': 'CAP/USDT',
            'action': 'CLOSE_LONG',
            'status': 'CLOSED',
            'reason': 'Channel Swing EXIT_LONG_ON_UPPER_WICK_REJECTION',
        }],
    )

    decision = pipeline.authorize(
        None, frame, 100.2, symbol='CAP/USDT', requested_side='SHORT',
        account=account,
    )

    assert decision is not None
    assert decision['type'] == 'AUTHORIZED_BY_PEAK_FLIP_SHORT'
    assert decision['side'] == 'SHORT'
    assert decision['override_cooldown'] is True
    assert decision['reverse_close_id'] == t0 + 180000 + 1000
    assert decision['entry_atr'] == 1.0
    assert decision['close_price'] == 100.2
    assert decision['pair_confirmation_bar_id'] == decision['confirmation_bar_id']

    from core.services.entry_contract import evaluate_entry_contract
    boundary_decision = evaluate_entry_contract(
        frame, 100.2, account=account, symbol='CAP/USDT',
    )
    assert boundary_decision is not None
    assert boundary_decision['type'] == 'AUTHORIZED_BY_PEAK_FLIP_SHORT'
    final_decision = evaluate_entry_contract(
        frame, 100.2, code='AUTHORIZED_BY_PEAK_FLIP_SHORT',
        account=account, symbol='CAP/USDT',
    )
    assert final_decision is not None
    assert final_decision['pending_signal_id'] == decision['pending_signal_id']

    from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
    engine = SimpleNamespace(
        account=account,
        _market_crash_entries_paused=lambda *_: True,
        _market_crash_entry_cooldown_until=10**12,
    )
    accepted, code, context = UnifiedEntryStrategy().evaluate_entry(
        frame, 100.2, 'SHORT', engine=engine, symbol='CAP/USDT',
    )
    assert accepted is True
    assert code == 'AUTHORIZED_BY_PEAK_FLIP_SHORT'
    assert context['reverse_close_id'] == decision['reverse_close_id']

    import asyncio
    from unittest.mock import AsyncMock
    from core.engine import TradingEngine
    trading_engine = object.__new__(TradingEngine)
    trading_engine.account = account
    trading_engine._market_crash_entries_paused = lambda *_: True
    trading_engine._place_structured_entry = AsyncMock(return_value=True)
    filled = asyncio.run(trading_engine._execute_confirmed_channel_break(
        'CAP/USDT', frame, 100.2, 'SHORT',
        v8_reason='AUTHORIZED_BY_PEAK_FLIP_SHORT',
        candidate_bar_id=decision['confirmation_bar_id'],
    ))
    assert filled is True
    trading_engine._place_structured_entry.assert_awaited_once()
    submitted_signal = trading_engine._place_structured_entry.await_args.args[1]
    assert submitted_signal['_is_authorized'] is True

    trading_engine._place_structured_entry.reset_mock()
    trading_engine.account = SimpleNamespace(
        positions={}, trades=[], log=lambda *args: None,
    )
    generic_flip = asyncio.run(trading_engine._execute_confirmed_channel_break(
        'CAP/USDT', frame, 100.2, 'SHORT',
        v8_reason='AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
        candidate_bar_id=decision['confirmation_bar_id'],
    ))
    assert generic_flip is False
    trading_engine._place_structured_entry.assert_not_awaited()


def test_peak_flip_short_is_limited_to_close_bar_and_next_bar():
    from types import SimpleNamespace

    t0 = 1_700_000_000_000
    rows = [
        {'timestamp': t0, 'open': 102.0, 'high': 102.2, 'low': 101.7, 'close': 101.8, 'atr': 1.0, 'kc_middle': 102.0, 'kc_upper': 104.0, 'kc_lower': 100.0, 'ma3': 102.0, 'ma5': 102.0, 'ma15': 103.0, 'is_closed': True},
        {'timestamp': t0 + 60000, 'open': 101.8, 'high': 102.0, 'low': 101.1, 'close': 101.2, 'atr': 1.0, 'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8, 'ma3': 101.8, 'ma5': 101.7, 'ma15': 102.8, 'is_closed': True},
        {'timestamp': t0 + 120000, 'open': 101.2, 'high': 101.4, 'low': 100.7, 'close': 100.8, 'atr': 1.0, 'kc_middle': 101.5, 'kc_upper': 103.5, 'kc_lower': 99.5, 'ma3': 101.5, 'ma5': 101.3, 'ma15': 102.5, 'is_closed': True},
        {'timestamp': t0 + 240000, 'open': 100.8, 'high': 100.9, 'low': 100.1, 'close': 100.8, 'atr': 1.0, 'kc_middle': 101.2, 'kc_upper': 103.2, 'kc_lower': 99.2, 'ma3': 101.2, 'ma5': 100.5, 'ma15': 102.2, 'is_closed': False},
    ]
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60000
    close_trade = {
        'id': t0 + 181000, 'symbol': 'CAP/USDT',
        'action': 'CLOSE_LONG', 'status': 'CLOSED',
        'reason': 'Channel Swing EXIT_LONG_ON_UPPER_WICK_REJECTION',
    }
    account = SimpleNamespace(positions={}, trades=[close_trade])

    next_bar = pipeline.detect_peak_flip_short(frame, 100.2, account, 'CAP/USDT')
    assert next_bar is not None

    frame.loc[3, 'timestamp'] = t0 + 300000
    expired = pipeline.detect_peak_flip_short(frame, 100.2, account, 'CAP/USDT')
    assert expired is None

    close_trade['reason'] = '手動平倉'
    frame.loc[3, 'timestamp'] = t0 + 240000
    manual_close = pipeline.detect_peak_flip_short(frame, 100.2, account, 'CAP/USDT')
    assert manual_close is None


def test_long_upper_wick_rejection_exits_profitable_long():
    """A long upper wick after a meaningful long profit triggers the long-specific exit."""
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 101.0, 'low': 99.5, 'close': 100.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 100.2, 'ma15': 99.8},
        {'timestamp': 2000, 'open': 100.5, 'high': 102.0, 'low': 100.0, 'close': 101.5, 'atr': 1.0, 'kc_middle': 100.5, 'kc_upper': 102.5, 'kc_lower': 98.5, 'ma5': 100.8, 'ma15': 100.0},
        {'timestamp': 3000, 'open': 101.0, 'high': 105.0, 'low': 100.8, 'close': 101.2, 'atr': 1.0, 'kc_middle': 101.0, 'kc_upper': 103.0, 'kc_lower': 99.0, 'ma5': 101.0, 'ma15': 100.2},
    ]
    position = {'side': 'LONG', 'entry_price': 99.0, 'highest_price': 105.0}

    exit_reason, exit_info = PeakValleyExit.evaluate(position, pd.DataFrame(bars), quote=101.2)

    assert exit_reason == 'EXIT_LONG_ON_UPPER_WICK_REJECTION'
    assert exit_info['upper_wick'] >= 0.5 * exit_info['atr']
    assert exit_info['upper_wick'] > 2.0 * exit_info['body']
    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, pd.DataFrame(bars), 101.2, exit_reason, exit_info,
    )
    assert allowed is True


def test_0758_lobster_breakout_authorized_via_evaluate_entry_contract():
    """10. 驗收 07:58 龍蝦大陽線：evaluate_entry_contract 絕不被 three_bar_rail_gate 阻擋，直接授權開多。"""
    from core.services.entry_contract import evaluate_entry_contract
    atr = 1.0
    t0 = 1700000000000
    bars = [
        {'timestamp': t0, 'open': 100.0, 'high': 100.5, 'low': 99.8, 'close': 100.4, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 101.5, 'kc_lower': 98.5, 'ma5': 100.2, 'ma15': 99.8, 'is_closed': True},
        {'timestamp': t0 + 60000, 'open': 100.4, 'high': 100.8, 'low': 100.2, 'close': 100.7, 'atr': atr, 'kc_middle': 100.2, 'kc_upper': 101.6, 'kc_lower': 98.6, 'ma5': 100.4, 'ma15': 99.9, 'is_closed': True},
        # 07:58 龍蝦飽滿大陽線 (尚未收線)：open=100.7, low=100.6, high=102.5, live quote=102.4
        # KC_upper = 101.8. Quote 102.4 > 101.8, MA5=101.2 > MA15=100.0, 實體佔比 94%
        {'timestamp': t0 + 120000, 'open': 100.7, 'high': 102.5, 'low': 100.6, 'close': 102.4, 'is_closed': False, 'atr': atr, 'kc_middle': 100.5, 'kc_upper': 101.8, 'kc_lower': 98.8, 'ma5': 101.2, 'ma15': 100.0},
    ]
    df = pd.DataFrame(bars)
    df.attrs['timeframe_ms'] = 60000
    live_quote = 102.4

    diagnostics = {}
    entry = evaluate_entry_contract(df, live_quote, symbol='LOBSTER/USDT', diagnostics=diagnostics)
    assert entry is not None, f"Expected entry authorized, got diagnostics: {diagnostics}"
    assert entry['side'] == 'LONG'
    assert entry['type'] in ('AUTHORIZED_REALTIME_BREAKOUT', 'AUTHORIZED_BY_TREND_CONTINUATION_LONG')
    assert diagnostics.get('reason') != 'WAIT_THREE_BAR_BREAKOUT_CONFIRMATION'
    assert diagnostics.get('reason') != 'BLOCKED_THREE_BAR_LONG_NOT_BROKEN_UPPER_RAIL'


def test_0810_cap_reversal_flip_authorized_via_evaluate_entry_contract():
    """11. 驗收 08:10 CAP 頂部反手大陰線：evaluate_entry_contract 立即秒換向授權開空。"""
    from core.services.entry_contract import evaluate_entry_contract
    atr = 1.0
    t0 = 1700000000000
    bars = [
        {'timestamp': t0, 'open': 101.0, 'high': 103.0, 'low': 100.8, 'close': 102.8, 'atr': atr, 'kc_middle': 101.5, 'kc_upper': 106.0, 'kc_lower': 97.0, 'ma5': 102.0, 'ma15': 100.0, 'is_closed': True},
        {'timestamp': t0 + 60000, 'open': 102.8, 'high': 105.0, 'low': 102.5, 'close': 104.5, 'atr': atr, 'kc_middle': 102.0, 'kc_upper': 106.5, 'kc_lower': 97.5, 'ma5': 103.5, 'ma15': 100.5, 'is_closed': True},
        # 08:10 頂部反手大陰線摜破 MA5 (102.8) 與中軌 (102.0)：open=104.5, high=104.6, low=101.5, live quote=101.8
        {'timestamp': t0 + 120000, 'open': 104.5, 'high': 104.6, 'low': 101.5, 'close': 101.8, 'is_closed': False, 'atr': atr, 'kc_middle': 102.0, 'kc_upper': 106.5, 'kc_lower': 97.5, 'ma5': 102.8, 'ma15': 100.8},
    ]
    df = pd.DataFrame(bars)
    df.attrs['timeframe_ms'] = 60000
    live_quote = 101.8

    diagnostics = {}
    entry = evaluate_entry_contract(df, live_quote, symbol='CAP/USDT', diagnostics=diagnostics)
    assert entry is not None, f"Expected entry authorized, got diagnostics: {diagnostics}"
    assert entry['side'] == 'SHORT'
    assert entry['type'] == 'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT'
    assert entry.get('override_cooldown') is not True


def test_three_bar_rail_gate_is_completely_bypassed():
    """12. 驗收 three_bar_rail_gate 已完全除役 (Fail-Open)，絕不阻擋任何即時行情。"""
    from core.services.three_bar_rail_gate import three_bar_rail_gate_problem
    df = pd.DataFrame([{'open': 100, 'close': 101}])
    assert three_bar_rail_gate_problem(df, 101.0, 'LONG') is None
    assert three_bar_rail_gate_problem(df, 99.0, 'SHORT') is None


def test_exit_gate_case_a_long_pullback_to_ma5_blocked():
    """用例 A：多單開倉後微幅回踩 MA5（未觸發止損、非大瀑布），斷言【平倉被阻擋，維持持倉】。"""
    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    atr = 1.0
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 101.0, 'low': 99.8, 'close': 100.8, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 100.4, 'ma15': 99.8},
        {'timestamp': 2000, 'open': 100.8, 'high': 101.5, 'low': 100.4, 'close': 101.2, 'atr': atr, 'kc_middle': 100.2, 'kc_upper': 102.2, 'kc_lower': 98.2, 'ma5': 100.8, 'ma15': 100.0},
        # 當前棒微幅回踩 MA5 (100.8)，報價 100.75，非大瀑布 (跌幅 0.45 < 1.2*ATR)，未觸發硬止損
        {'timestamp': 3000, 'open': 101.2, 'high': 101.3, 'low': 100.7, 'close': 100.75, 'atr': atr, 'kc_middle': 100.3, 'kc_upper': 102.3, 'kc_lower': 98.3, 'ma5': 100.8, 'ma15': 100.1},
    ]
    df = pd.DataFrame(bars)
    position = {'side': 'LONG', 'entry_price': 100.5, 'highest_price': 101.5, 'symbol': 'CAP/USDT'}
    quote = 100.75

    # 任何常規回踩平倉訊號（如 MA5 回踩、微動能拐頭）
    candidate_reason = 'MA5_PULLBACK_EXIT'
    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(position, df, quote, candidate_reason)

    assert allowed is False
    assert reason == HoldingProtectionExitGate.REJECT_REASON


def test_exit_gate_case_b_short_upper_wick_pullback_blocked():
    """用例 B：空單開倉後遭遇長上影線回踩，斷言【平倉被阻擋，維持持倉】。"""
    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    atr = 1.0
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 100.2, 'low': 98.5, 'close': 99.0, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 99.5, 'ma15': 100.2},
        {'timestamp': 2000, 'open': 99.0, 'high': 99.2, 'low': 97.8, 'close': 98.0, 'atr': atr, 'kc_middle': 99.8, 'kc_upper': 101.8, 'kc_lower': 97.8, 'ma5': 98.8, 'ma15': 100.0},
        # 空單遭遇長上影線衝高回落 (high=98.8, open=98.0, quote=98.1, upper_wick=0.7 >= 0.4*ATR)
        {'timestamp': 3000, 'open': 98.0, 'high': 98.8, 'low': 97.7, 'close': 98.1, 'atr': atr, 'kc_middle': 99.5, 'kc_upper': 101.5, 'kc_lower': 97.5, 'ma5': 98.4, 'ma15': 99.8},
    ]
    df = pd.DataFrame(bars)
    position = {'side': 'SHORT', 'entry_price': 100.0, 'lowest_price': 97.8, 'symbol': 'CAP/USDT'}
    quote = 98.1

    # 模擬上影線抖動或常規回踩平倉訊號
    candidate_reason = 'EXIT_SHORT_ON_UPPER_WICK'
    details = {'upper_wick_hold': True}
    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(position, df, quote, candidate_reason, details=details)

    assert allowed is False
    assert reason == HoldingProtectionExitGate.WAIT_CLOSE_REJECT_REASON


def test_exit_gate_case_c_short_ratchet_profit_lock_triggers():
    """用例 C：空單浮盈達 5%，隨後回吐至鎖利線，斷言【順暢觸發 EXIT_BY_RATCHET_PROFIT_LOCK】。"""
    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    atr = 1.0
    bars = [
        {'timestamp': 1000, 'open': 100.0, 'high': 100.2, 'low': 97.0, 'close': 97.5, 'atr': atr, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0, 'ma5': 98.5, 'ma15': 100.2},
        {'timestamp': 2000, 'open': 97.5, 'high': 97.6, 'low': 95.0, 'close': 95.2, 'atr': atr, 'kc_middle': 99.0, 'kc_upper': 101.0, 'kc_lower': 97.0, 'ma5': 97.0, 'ma15': 99.5},
        {'timestamp': 3000, 'open': 95.2, 'high': 96.6, 'low': 95.0, 'close': 96.5, 'is_closed': False, 'atr': atr, 'kc_middle': 98.5, 'kc_upper': 100.5, 'kc_lower': 96.5, 'ma5': 96.2, 'ma15': 99.0},
    ]
    df = pd.DataFrame(bars)
    # entry_price = 100.0, lowest_price = 95.0 -> 波段峰值 ROE = (100 - 95) / 100 = 5.0% (>= 3.5%)
    position = {'side': 'SHORT', 'entry_price': 100.0, 'lowest_price': 95.0, 'symbol': 'CAP/USDT'}
    # 隨後回吐至 96.5 -> 回吐量 = (96.5 - 95.0) = 1.5, 回吐比例 = 1.5 / 5.0 = 30.0% (> 25%)
    quote = 96.5

    # 1. 斷言 evaluate 直接檢測出 EXIT_BY_RATCHET_PROFIT_LOCK
    eval_reason, eval_info = HoldingProtectionExitGate.evaluate(position, df, quote)
    assert eval_reason == 'EXIT_BY_RATCHET_PROFIT_LOCK'
    assert eval_info.get('peak_roe') >= 0.05
    assert eval_info.get('giveback_ratio') >= 0.25

    # 2. 斷言 validate_exit 順暢放行 EXIT_BY_RATCHET_PROFIT_LOCK
    allowed, validated_reason, _ = HoldingProtectionExitGate.validate_exit(
        position, df, quote, candidate_reason='EXIT_BY_RATCHET_PROFIT_LOCK', details=eval_info
    )
    assert allowed is True
    assert validated_reason == 'EXIT_BY_RATCHET_PROFIT_LOCK'
