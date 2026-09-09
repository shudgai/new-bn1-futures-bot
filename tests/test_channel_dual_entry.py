"""Authorized pivots plus closed outer break entries share order safeguards."""
import asyncio
import pytest
from core.engine import TradingEngine
from test_channel_symmetric_rules import market
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('invalid', ['none', 'live_only', 'doji', 'wick', 'flat_ma15', 'opposite_ma15', 'slope', 'invalid_data'])
def test_outer_break_confirmation_and_direction(side, invalid):
    f = market(side); price = float(f.iloc[-1]['close'])
    if invalid == 'live_only':
        f = f.iloc[:-1].copy()
    elif invalid == 'doji':
        f.loc[18, 'open'] = f.loc[18, 'close']
    elif invalid == 'wick':
        f.loc[17, 'close'] = 101.5 if side == 'LONG' else 98.5
    elif invalid == 'flat_ma15':
        f.loc[16:18, 'ma15'] = 100.
    elif invalid == 'opposite_ma15':
        f.loc[16:18, 'ma15'] = f.loc[16:18, 'ma15'].to_numpy()[::-1]
    elif invalid == 'slope':
        rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
        f.loc[17, rail] += .2 if side == 'LONG' else -.2
    elif invalid == 'invalid_data':
        f.loc[16, 'ma3'] = float('nan')
    decision = TradingEngine._channel_swing_action(f, price)
    assert (decision['action'] == 'ENTER') is (invalid == 'none'), decision
    if invalid == 'none':
        assert decision['side'] == side
        assert decision['reason'] == ('KC_UPPER_BREAKOUT_STRICT' if side == 'LONG' else 'KC_LOWER_BREAKOUT_STRICT')


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('reentry', [False, True])
async def test_outer_break_order_and_profit_reentry_fill_once(side, reentry, monkeypatch):
    f = market(side); price = float(f.iloc[-1]['close'])
    e = _execution_engine(f, side, True)
    e.account.positions.clear(); e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    if reentry:
        e.account.channel_profit_reentries = {SYMBOL: dict(side=side, phase='closed', token='previous', exit_bar_id=19)}
        await e._process_single_symbol(SYMBOL, 0., None, False)
        assert not e.account.events  # only a new post-close confirmation can enter
        f.index += 1
    await asyncio.gather(*(e._process_single_symbol(SYMBOL, 1., None, False) for _ in range(3)))
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][2] == side
    assert SYMBOL not in e.account.channel_profit_reentries
    if reentry:
        assert any('新順勢外軌突破' in text for text, _ in e.account.logs)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_outer_snapshot_rejects_changed_direction(side):
    f = market(side); price = float(f.iloc[-1]['close'])
    e = _execution_engine(f, side, True)
    e.account.positions.clear(); e.tickers[SYMBOL] = price
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, 18) is not None
    f.loc[16:18, 'ma15'] = 100.
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, 18) is None
