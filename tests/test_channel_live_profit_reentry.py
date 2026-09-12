"""Profit reentry requires two completed directional bodies on both sides."""
from unittest.mock import AsyncMock
import pytest
from core.services.strategies.outer_strategy import two_closed_bodies_ready, outside_reentry
from test_channel_outer_cycle import setup
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['normal', 'legacy_profit', 'abnormal', 'recovered', 'same_bar', 'fresh_red', 'fresh_ck', 'fresh_ma', 'room'])
async def test_live_reentry_without_closed_bodies(side, case, monkeypatch):
    f, price = setup(side)
    f.loc[17:18, 'open'] = f.loc[17:18, 'close']
    assert not two_closed_bodies_ready(f, side)
    # 2026-09-14：延續不看前一根的顏色/實體 → 已收線沒有實體K不再阻擋。
    assert outside_reentry(f, price, side)['side'] == side
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e._channel_profit_room = lambda *a: dict(allowed=case != 'room', reason='KC_PROFIT_ROOM_INSUFFICIENT', net_room_pct=1., target=price)
    ticket = dict(side=side, token='live', phase='closed', mode='outer_cycle',
                  requires_pullback=case in ('abnormal', 'recovered', 'legacy_profit'), exit_bar_id=18)
    if case == 'legacy_profit': ticket['opened_at'] = 1
    if case == 'same_bar': ticket['exit_bar_id'] = 19
    if case == 'recovered':
        ticket.update(exit_bar_id=17, pullback_bar=18)
    e.account.channel_profit_reentries = {SYMBOL: ticket}
    fresh = f.copy()
    if case == 'fresh_red': fresh.loc[19, 'open'] = price + (1 if side == 'LONG' else -1)
    if case == 'fresh_ck': fresh['kc_middle'] = 100.
    if case == 'fresh_ma': fresh.loc[16, 'close'] = price
    e.fetch_klines = AsyncMock(return_value=fresh)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await e._try_profit_reentry(SYMBOL, f, price, False)
    blocked = case in ('abnormal', 'same_bar', 'fresh_ck', 'fresh_ma', 'fresh_red', 'room',
                       'normal', 'legacy_profit', 'recovered')
    assert bool(e.account.events) is (not blocked), (case, e.account.logs)

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['valid', 'first_opposite', 'second_opposite', 'doji', 'small_body', 'invalid', 'no_cross', 'confirmation_inside'])
async def test_reentry_rechecks_closed_bodies_before_order(side, case, monkeypatch):
    f, price = setup(side)
    sign = 1 if side == 'LONG' else -1
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e._channel_profit_room = lambda *a: dict(allowed=True, net_room_pct=1., target=price)
    e.account.channel_profit_reentries = {SYMBOL: dict(side=side, token='fresh', phase='closed',
        mode='outer_cycle', requires_pullback=False, exit_bar_id=18)}
    fresh = f.copy()
    if case == 'first_opposite': fresh.loc[17, 'open'] = fresh.loc[17, 'close'] + sign * .1
    if case == 'second_opposite': fresh.loc[18, 'open'] = fresh.loc[18, 'close'] + sign * .1
    if case == 'doji': fresh.loc[18, 'open'] = fresh.loc[18, 'close']
    if case == 'small_body': fresh.loc[18, 'open'] = fresh.loc[18, 'close'] - sign * .01
    if case == 'invalid': fresh.loc[18, 'high'] = float('nan')
    if case == 'no_cross': fresh.loc[17, 'open'] = fresh.loc[17, 'kc_upper' if side == 'LONG' else 'kc_lower'] + sign * .1
    if case == 'confirmation_inside': fresh.loc[18, 'kc_upper' if side == 'LONG' else 'kc_lower'] = price + sign
    e.fetch_klines = AsyncMock(return_value=fresh)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await e._try_profit_reentry(SYMBOL, f, price, False)
    assert len(e.account.events) == int(case == 'valid'), e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("cached", [False, True])
async def test_order_cannot_bypass_body_cross_with_two_colored_bodies(side, cached, monkeypatch):
    from core.services.strategies.outer_strategy import confirmed_outer_breakout_ready
    f, price = setup(side)
    sign = 1 if side == "LONG" else -1
    rail = "kc_upper" if side == "LONG" else "kc_lower"
    f.loc[17, "open"] = f.loc[17, rail] + sign * .1
    assert two_closed_bodies_ready(f, side)
    assert not confirmed_outer_breakout_ready(f, price, side)
    assert outside_reentry(f, price, side)["side"] is None
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    snapshot = dict(price=price, kc_upper=float(f.iloc[-1]["kc_upper"]),
                    kc_lower=float(f.iloc[-1]["kc_lower"]), frame=f)
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    signal = dict(side=side, entry_mode="CHANNEL_SWING", action="ENTER_MARKET")
    assert not await e._place_structured_entry(
        SYMBOL, signal, price, channel_snapshot=snapshot if cached else None)
    assert not e.account.events


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("legacy_pending", [False, True])
async def test_middle_touch_and_legacy_pending_never_close(side, legacy_pending):
    f, _ = setup(side)
    e = _execution_engine(f, side, True)
    e.account.save_state = lambda: None
    sign = 1 if side == "LONG" else -1
    e.account.positions[SYMBOL].update(entry_price=100 + sign * 10, qty=1.,
        open_timestamp=1., channel_pivot_middle_exit_pending=legacy_pending)
    e._channel_swing_action = lambda *a, **k: dict(action="EXIT", reason="KC_REACHED_MIDDLE_COMPRESSED")
    e.tickers[SYMBOL] = 100.
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert not e.account.events, e.account.logs
    assert SYMBOL in e.account.positions
    assert not any("處理失敗" in message for message, _ in e.account.logs)
