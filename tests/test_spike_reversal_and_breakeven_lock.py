import pytest
import pandas as pd
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


def test_long_spike_reversal_harvest():
    """驗證 CAP 式衝天避雷針：浮盈 >= 1.5 ATR 後高檔跳水或長上影線 >= 50%，立即平多收割。"""
    strategy = DualTrackExitStrategy()
    position = {
        'symbol': 'CAP/USDT',
        'side': 'LONG',
        'entry_price': 0.0885,
        'entry_atr': 0.0005,
        'highest_price': 0.0898,  # 浮盈 (0.0898 - 0.0885) = 0.0013 = 2.6 ATR >= 1.5 ATR
        'open_timestamp': 1000,
        'qty': 1000.0,
    }

    # 1m 當根高點 0.0898，跳水回落到 0.0890，上影線 (0.0898 - 0.0890) = 0.0008，振幅 0.0898 - 0.0886 = 0.0012，影線佔比 66% >= 50%
    # 且自最高點回撤 0.0008 = 1.6 ATR >= 1.2 ATR，浮盈回吐 0.0008 / 0.0013 = 61.5% >= 40%
    stamp = 60000
    frame = pd.DataFrame([
        dict(timestamp=stamp, is_closed=False, open=0.0888, high=0.0898, low=0.0886, close=0.0890,
             ma5=0.0892, ma15=0.0886, kc_middle=0.0886, atr=0.0005),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=0.0890, is_closed=False)
    assert exit_signal == 'EXIT_BY_SPIKE_REVERSAL'


def test_long_hard_breakeven_lock():
    """驗證絕對保本防線：浮盈曾達 >= 1.2 ATR，利潤回吐至成本 + 0.05% 時強制鎖死平倉，絕不轉虧。"""
    strategy = DualTrackExitStrategy()
    entry = 0.047955
    atr = 0.0003
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': entry,
        'entry_atr': atr,
        'highest_price': 0.048350,  # 浮盈曾達 1.3 ATR (介於 1.2 ~ 1.5 ATR，精準驗證保本防線)
        'open_timestamp': 1000,
        'qty': 1000.0,
    }

    # 價格回吐至保本底線以下 (entry * 1.0005 = 0.0479789)，當前價跌至 0.047970
    stamp = 60000
    frame = pd.DataFrame([
        dict(timestamp=stamp, is_closed=False, open=0.0480, high=0.0481, low=0.0479, close=0.047970,
             ma5=0.0480, ma15=0.0478, kc_middle=0.0475, atr=atr),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=0.047970, is_closed=False)
    assert exit_signal == 'EXIT_BY_BREAKEVEN_LOCK'
    assert position.get('breakeven_locked') is True


def test_lobster_true_peak_reversal_kc_down():
    """驗證龍蝦實盤：價格摜穿 KC 中軌且通道轉為「KC向下」，真峰頂確認立即一票平多。"""
    strategy = DualTrackExitStrategy()
    entry = 0.047955
    atr = 0.0003
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': entry,
        'entry_atr': atr,
        'highest_price': 0.048600,
        'open_timestamp': 1000,
        'qty': 1000.0,
    }

    stamp = 60000
    # 跌到 0.047630，摜穿中軌 0.0480，通道標記「KC向下」
    frame = pd.DataFrame([
        dict(timestamp=stamp-60000, is_closed=True, open=0.0484, high=0.0486, low=0.0481, close=0.0482,
             ma5=0.0483, ma15=0.0481, kc_middle=0.0482, atr=atr, channel_state="KC向上"),
        dict(timestamp=stamp, is_closed=True, open=0.0482, high=0.0482, low=0.0476, close=0.047630,
             ma5=0.0478, ma15=0.0480, kc_middle=0.0480, atr=atr, channel_state="KC向下"),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=0.047630, is_closed=True)
    assert exit_signal == 'EXIT_BY_CONFIRMED_TRUE_PEAK'


def test_lobster_peak_requires_fifteen_completed_bars():
    """不足 15 根已收線資料時，不能把長上影或盤中陰線當成真峰頂。"""
    strategy = DualTrackExitStrategy()
    entry = 0.047955
    atr = 0.0003
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': entry,
        'entry_atr': atr,
        'highest_price': 0.048600,
        'open_timestamp': 1000,
        'qty': 1000.0,
    }

    stamp = 180000
    # 前兩根帶明顯長上影線（高點 0.0486，收盤 0.0483，上影線 0.0003 = 1.0 ATR >= 0.35 ATR）
    # 第三根大陰線 open 0.0484, close 0.0481 (跌幅 0.0003 = 1.0 ATR >= 0.5 ATR)，直接跌破 MA5 (0.0483)
    frame = pd.DataFrame([
        dict(timestamp=stamp-120000, is_closed=True, open=0.0482, high=0.0486, low=0.0481, close=0.0483,
             ma5=0.0483, ma15=0.0480, kc_middle=0.0481, atr=atr),
        dict(timestamp=stamp-60000, is_closed=True, open=0.0483, high=0.04855, low=0.0482, close=0.0483,
             ma5=0.0483, ma15=0.0480, kc_middle=0.0481, atr=atr),
        dict(timestamp=stamp, is_closed=False, open=0.0484, high=0.04845, low=0.0481, close=0.04815,
             ma5=0.0483, ma15=0.0480, kc_middle=0.0481, atr=atr),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=0.04815, is_closed=False)
    assert exit_signal is None


def test_short_spike_reversal_harvest():
    """驗證空單極端探底抽針：浮盈 >= 1.5 ATR 後低檔大抽針，立即平空收割。"""
    strategy = DualTrackExitStrategy()
    position = {
        'symbol': 'BTC/USDT',
        'side': 'SHORT',
        'entry_price': 100.0,
        'entry_atr': 1.0,
        'lowest_price': 97.0,  # 浮盈曾達 3.0 ATR >= 1.5 ATR
        'open_timestamp': 1000,
        'qty': 1.0,
    }

    # 自最低點 97.0 反彈至 98.5 (反彈 1.5 ATR >= 1.2 ATR 且回吐 50% >= 40%)
    stamp = 60000
    frame = pd.DataFrame([
        dict(timestamp=stamp, is_closed=False, open=97.5, high=98.8, low=97.0, close=98.5,
             ma5=98.0, ma15=99.0, kc_middle=99.5, atr=1.0),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=98.5, is_closed=False)
    assert exit_signal == 'EXIT_BY_SPIKE_REVERSAL'


def test_short_hard_breakeven_lock():
    """驗證空單絕對保本防線：浮盈曾達 >= 1.2 ATR，價格反彈至 entry * 0.9995 時強制鎖死平倉。"""
    strategy = DualTrackExitStrategy()
    entry = 100.0
    atr = 1.0
    position = {
        'symbol': 'BTC/USDT',
        'side': 'SHORT',
        'entry_price': entry,
        'entry_atr': atr,
        'lowest_price': 98.7,  # 浮盈曾達 1.3 ATR >= 1.2 ATR
        'open_timestamp': 1000,
        'qty': 1.0,
    }

    # 空單保本線 entry * 0.9995 = 99.95，當前價反彈至 99.96
    stamp = 60000
    frame = pd.DataFrame([
        dict(timestamp=stamp, is_closed=False, open=99.5, high=100.0, low=99.2, close=99.96,
             ma5=99.7, ma15=100.2, kc_middle=100.5, atr=atr),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=99.96, is_closed=False)
    assert exit_signal == 'EXIT_BY_BREAKEVEN_LOCK'
    assert position.get('breakeven_locked') is True


def test_short_lock_exit_does_not_require_a_kc_middle_break():
    """階梯鎖利可獨立出場；KC 中軌不是平空前置條件。"""
    strategy = DualTrackExitStrategy()
    entry = 100.0
    atr = 1.0
    position = {
        'symbol': 'BTC/USDT',
        'side': 'SHORT',
        'entry_price': entry,
        'entry_atr': atr,
        'lowest_price': 97.0,
        'open_timestamp': 1000,
        'qty': 1.0,
    }

    stamp = 60000
    frame = pd.DataFrame([
        dict(timestamp=stamp-60000, is_closed=True, open=98.0, high=98.5, low=97.0, close=97.2,
             ma5=97.5, ma15=98.5, kc_middle=98.0, atr=atr, channel_state="KC向下"),
        dict(timestamp=stamp, is_closed=True, open=97.2, high=99.2, low=97.0, close=98.8,
             ma5=98.2, ma15=98.5, kc_middle=98.0, atr=atr, channel_state="KC向上"),
    ])

    exit_signal = strategy.evaluate_exit(position, frame=frame, current_price=98.8, is_closed=True)
    assert exit_signal == 'EXIT_BY_RATCHET_PROFIT_LOCK'
