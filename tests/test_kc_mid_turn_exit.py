"""Closed KC slope exits, fixed-TP retirement, and production dispatch."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits.profit_protection_service import protection, ProfitProtectionExitStrategy
from core.services.symbol_runner import process_single_symbol_runner


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()


def position(side='LONG'):
    return dict(symbol='TEST/USDT', side=side, entry_price=100., qty=.1,
                open_timestamp=1, entry_atr=1.)


def frame(mids, price=100.):
    return pd.DataFrame([dict(timestamp=i*60000, open=price, close=price,
                              high=price, low=price, kc_middle=mid,
                              kc_upper=mid+10, kc_lower=mid-10, atr=1.)
                         for i, mid in enumerate(mids)])


@pytest.mark.parametrize('side,mids,price', [('LONG', [100, 99, 120], 102.5),
                                          ('SHORT', [100, 101, 80], 97.5)])
def test_closed_mid_reversal_exits_even_with_favorable_price(side, mids, price):
    p = position(side)
    strategy = ProfitProtectionExitStrategy(fee=0, slippage=0)
    assert strategy.evaluate_exit(p, frame(mids, price), price) == 'KC_CK_DIRECTION_REVERSED_EXIT'
    # Reload persisted state, lose the transient override, then recover slope.
    p = copy.deepcopy(p)
    p.pop('exit_reason_override')
    assert strategy.evaluate_exit(p, frame([100, 100, 100], price), price) == 'KC_CK_DIRECTION_REVERSED_EXIT'


@pytest.mark.parametrize('side,mids,price', [('LONG', [100, 101, 90], 103),
                                          ('SHORT', [100, 99, 110], 97),
                                          ('LONG', [100, 101, 90], 100),
                                          ('SHORT', [100, 99, 110], 100),
                                          ('LONG', [100, 100, 90], 100),
                                          ('SHORT', [100, 100, 110], 100)])
def test_no_fixed_tp_price_cross_or_live_mid_exit(side, mids, price):
    assert ProfitProtectionExitStrategy(fee=0, slippage=0).evaluate_exit(
        position(side), frame(mids, price), price) is None


@pytest.mark.parametrize('mids', [[100], [100, 99], [100, float('nan'), 100],
                                [100, float('inf'), 100], [100, 0, 100]])
def test_invalid_mid_does_not_create_exit(mids):
    assert protection(position(), 100, 0, 0, frame(mids)) is None


def test_ema_fallback_and_missing_mid():
    f = frame([100, 99, 100]).rename(columns={'kc_middle': 'ema_20'})
    assert protection(position(), 100, 0, 0, f)['reason'] == 'KC_CK_DIRECTION_REVERSED_EXIT'
    assert protection(position(), 100, 0, 0, f.drop(columns='ema_20')) is None


@pytest.mark.parametrize('side,price', [('LONG', 98.5), ('SHORT', 101.5)])
def test_fixed_stop_removed(side, price):
    p = position(side)
    p['atr_sl'] = price
    assert ProfitProtectionExitStrategy(fee=0, slippage=0).evaluate_exit(p, frame([100]*3, price), price) is None
    assert 'atr_sl' not in p


@pytest.mark.parametrize('reason', ['EXIT_STOP_LOSS: LONG 觸及 1.5 ATR 止損 (98.5)',
                                  'KC_CK_DIRECTION_REVERSED_EXIT',
                                  'EMERGENCY_STOP_LOSS: existing'])
def test_pending_migration_only_cancels_fixed_atr_stop(reason):
    p = position()
    f = frame([100]*3)
    protection(p, 101, 0, 0, f)
    p['channel_profit_protection'].update(pending=True, reason=reason)
    result = protection(p, 100, 0, 0, f)
    if reason.startswith('EXIT_STOP_LOSS:'):
        assert result is None
        assert not p['channel_profit_protection']['pending']
    else:
        assert result['reason'] == reason
    assert p['channel_profit_protection']['peak_net'] == pytest.approx(.1)


@pytest.mark.parametrize('side,sign', [('LONG', 1), ('SHORT', -1)])
def test_step_lock_preserves_peak_and_requires_four_usdt(side, sign):
    p = position(side)
    p.update(qty=1, entry_atr=10)
    f = frame([100]*3)
    assert protection(p, 100+sign*2, 0, 0, f) is None
    assert protection(p, 100, 0, 0, f) is None
    assert protection(p, 100+sign*6, 0, 0, f) is None
    result = protection(p, 100+sign*4, 0, 0, f)
    assert result['reason'].startswith('EXIT_PROFIT_LOCK_STEP:')
    assert result['peak_net'] == 6 and result['locked_net'] == 4


def test_old_fixed_tp_pending_removed_without_resetting_peak():
    p = position()
    f = frame([100]*3)
    protection(p, 103, 0, 0, f)
    p['channel_profit_protection']['pending'] = True
    p['exit_reason_override'] = 'EXIT_TAKE_PROFIT: old 2 ATR target'
    p['atr_tp'] = 102
    assert protection(p, 100, 0, 0, f) is None
    assert p['channel_profit_protection']['peak_net'] == pytest.approx(.3)
    assert 'atr_tp' not in p and 'exit_reason_override' not in p


@pytest.mark.parametrize('atr', [0, 'bad', float('nan')])
def test_invalid_atr_does_not_disable_mid_exit_or_hard_stop(atr):
    p = position()
    p['entry_atr'] = atr
    f = frame([100, 99, 100]).drop(columns='atr')
    assert protection(p, 100, 0, 0, f)['reason'] == 'KC_CK_DIRECTION_REVERSED_EXIT'
    p = position()
    p.update(entry_atr=atr, qty=1)
    assert protection(p, 96, 0, 0, f)['reason'].startswith('EMERGENCY_STOP_LOSS:')


def test_staged_position_still_bypasses_legacy():
    p = position()
    p['use_staged_risk_engine'] = True
    assert ProfitProtectionExitStrategy().evaluate_exit(p, frame([100, 99, 100]), 96) is None
    assert 'channel_profit_protection' not in p


def test_runner_persists_reversal_and_retries_at_latest_quote(monkeypatch):
    monkeypatch.setattr('core.services.symbol_runner.enforce_hard_stop', AsyncMock(return_value=False))
    p = position()
    symbol = p['symbol']
    account = SimpleNamespace(positions={symbol: p}, position_meta={},
                              close_position=AsyncMock(return_value=False),
                              save_state=Mock(), log=Mock())
    engine = SimpleNamespace(account=account, _channel_exit_frames={}, _last_exit_bar_id={},
                             _take_over_manual_position=Mock(), get_velocity_drop_ratio=Mock(return_value=0))
    async def run():
        await process_single_symbol_runner(engine, symbol, 1, None, False,
                                           exit_frame=frame([100, 99, 100]), exit_quote=100, exit_only=True)
        saved = copy.deepcopy(account.position_meta[symbol]['channel_profit_protection'])
        assert saved['pending'] and saved['reason'] == 'KC_CK_DIRECTION_REVERSED_EXIT'
        account.positions[symbol] = position()  # Restore observations from metadata.
        await process_single_symbol_runner(engine, symbol, 2, None, False,
                                           exit_frame=frame([100, 101, 102], 100.5), exit_quote=100.5, exit_only=True)
    asyncio.run(run())
    assert account.close_position.await_count == 2
    assert account.close_position.await_args.args[1] == 100.5
    assert 'KC_CK_DIRECTION_REVERSED_EXIT' in account.close_position.await_args.args[2]
    assert not any('處理失敗' in str(call) for call in account.log.call_args_list)
