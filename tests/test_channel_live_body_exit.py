import pytest

from core.engine import TradingEngine
from test_channel_position_path import frame_for
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def exit_frame(side):
    f = frame_for(side)
    sign = 1 if side == 'LONG' else -1
    f.loc[3, 'ma3'] = 100 + sign * 3
    # Previous close remains on the holding side; no closed confirmation.
    f.loc[6, 'close'] = 100 + sign * .3
    f.loc[7, 'open'] = 100 + sign * .2
    f.loc[7, 'close'] = 100 - sign * .4
    return f


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('scale', [1., .000036])
@pytest.mark.parametrize('offset', [-.4, -.2, -.1, 0., .2, .4])
def test_strict_majority_color_and_wicks(side, scale, offset):
    f = exit_frame(side)
    sign = 1 if side == 'LONG' else -1
    # Wicks cross both sides but do not define the body.
    f.loc[7, ['high', 'low']] = [105., 95.]
    for col in ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_upper', 'kc_lower']:
        f[col] *= scale
    price = (100 + sign * offset) * scale
    result = TradingEngine._channel_swing_action(f, price, side, position_open_timestamp=120)
    assert result['action'] == ('EXIT' if offset < -.2 else 'HOLD')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('guard', ['no_path', 'preentry_path', 'ma3_outside', 'ma3_touch', 'space40', 'space50'])
def test_live_majority_retains_path_but_ignores_space(side, guard):
    f = exit_frame(side)
    sign = 1 if side == 'LONG' else -1
    if guard == 'no_path':
        f['ma3'] = 100.
    elif guard == 'preentry_path':
        f.loc[3, 'ma3'] = 100.
        f.loc[0, 'ma3'] = 100 + sign * 3
    elif guard in ['ma3_outside', 'ma3_touch']:
        f.loc[7, 'ma3'] = 100 + sign * (3 if guard == 'ma3_outside' else 2)
    else:
        ratio = .4 if guard == 'space40' else .5
        f.loc[7, 'ma15'] = 102 - 4*ratio if side == 'LONG' else 98 + 4*ratio
    result = TradingEngine._channel_swing_action(f, float(f.iloc[-1]['close']), side, position_open_timestamp=120)
    assert result['action'] == ('EXIT' if guard in ['space40', 'space50'] else 'HOLD')


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('success', [True, False])
async def test_live_majority_does_not_close_under_profit_only_rules(side, success):
    f = exit_frame(side)
    e = _execution_engine(f, side, success)
    e.account.positions[SYMBOL]['open_timestamp'] = 120
    e.account.save_state = lambda: None
    e.market_prebreakout_directions = {}
    e.tickers[SYMBOL] = float(f.iloc[-1]['close'])
    await e._process_single_symbol(SYMBOL, 500., None, False)
    # The user retired this ordinary exit for every style, even before arming.
    assert e.account.events == [], e.account.logs
    assert SYMBOL in e.account.positions
    assert SYMBOL not in getattr(e, '_channel_swing_peak_exit_info', {})
    assert not any('處理失敗' in message for message, _ in e.account.logs)
