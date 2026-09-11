import pytest
from core.engine import TradingEngine
from test_channel_position_path import frame_for
from test_channel_swing_execution import _execution_engine, SYMBOL


def falling_frame():
    f = frame_for('LONG')
    f['high'] = [104., 105., 104., 103., 104., 103., 102., 104.]
    f['low'] = [100., 99., 100., 98., 99., 97., 98., 100.]
    f['kc_middle'] = [100., 100., 100., 100., 101., 100., 99., 110.]
    f.loc[7, ['open', 'close']] = [101., 103.]
    return f


def test_falling_waves_block_live_long_without_opening_short():
    f = falling_frame()
    assert TradingEngine._channel_closed_waves_falling(f)
    assert TradingEngine._channel_swing_action(f, 103.) == {
        'action': 'WAIT', 'side': None, 'reason': 'KC_FALLING_WAVES_BLOCK_LONG',
    }


@pytest.mark.parametrize('missing', ['slope', 'peaks', 'troughs', 'equal_peak', 'equal_middle'])
def test_all_three_conditions_are_required(missing):
    f = falling_frame()
    if missing == 'slope':
        f.loc[4:6, 'kc_middle'] = [99., 100., 101.]
    elif missing == 'peaks':
        f.loc[4, 'high'] = 106.
    elif missing == 'troughs':
        f.loc[5, 'low'] = 98.5
        f.loc[6, 'low'] = 99.
    elif missing == 'equal_peak':
        f.loc[4, 'high'] = 105.
    else:
        f.loc[6, 'kc_middle'] = 100.
    assert not TradingEngine._channel_closed_waves_falling(f)
    assert TradingEngine._channel_swing_action(f, 103.)['side'] == 'LONG'


def test_unclosed_candle_cannot_confirm_second_peak():
    f = falling_frame()
    f['high'] = [101., 105., 101., 101., 101., 101., 104., 100.]
    assert not TradingEngine._channel_closed_waves_falling(f)


def test_live_candle_does_not_change_closed_wave_direction():
    f = falling_frame()
    f.loc[7, ['high', 'low', 'kc_middle']] = [1000., 1., 1000.]
    assert TradingEngine._channel_closed_waves_falling(f)


def test_no_automatic_short_and_existing_short_signal_still_allowed():
    f = falling_frame()
    assert TradingEngine._channel_swing_action(f, 100.)['action'] == 'WAIT'
    f.loc[7, ['open', 'close']] = [100., 97.]
    assert TradingEngine._channel_swing_action(f, 97.)['side'] == 'SHORT'


def test_held_long_is_not_closed_by_falling_waves():
    f = falling_frame()
    assert TradingEngine._channel_swing_action(f, 103., 'LONG', position_open_timestamp=120)['action'] == 'HOLD'


@pytest.mark.anyio
async def test_fresh_entry_snapshot_rechecks_falling_waves():
    f = falling_frame()
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = 103.
    assert await e._fresh_channel_entry_snapshot(SYMBOL, 'LONG') is None


@pytest.fixture
def anyio_backend():
    return 'asyncio'
