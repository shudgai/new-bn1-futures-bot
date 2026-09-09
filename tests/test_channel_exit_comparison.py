import numpy as np
import pandas as pd
import pytest
from core.engine import TradingEngine
from tools.channel_exit_comparison import signals, simulate, MINUTE, TAKER_FEE_RATE, SLIPPAGE_PCT


def candles(count=400):
    random = np.random.default_rng(917)
    close = 100 + np.cumsum(random.normal(0,.3,count))
    opens = np.r_[100,close[:-1]]
    return pd.DataFrame(dict(timestamp=np.arange(count)*MINUTE,open=opens,
        high=np.maximum(opens,close)+.1,low=np.minimum(opens,close)-.1,
        close=close,volume=np.full(count,1000.)))


def test_signals_match_live_engine_for_entries_and_reversals():
    f = signals(candles())
    for index in range(18,len(f)):
        frame = f.iloc[max(0,index-199):index+1]
        result = TradingEngine._channel_swing_action(frame,float(f.open.iloc[index]))
        assert f.entry.iloc[index] == {'LONG':1,'SHORT':-1,None:0}[result['side']]
        for old, direction in [('LONG',1),('SHORT',-1)]:
            result = TradingEngine._channel_swing_action(frame,float(f.open.iloc[index]),old)
            assert (result['action']=='REVERSE') == (f.break_side.iloc[index]==-direction)


def test_future_and_current_candle_cannot_change_signal():
    raw = candles()
    original = signals(raw)
    raw.loc[200:, ['open','high','low','close']] *= 2
    changed = signals(raw)
    for column in ['entry','break_side','peak_exit']:
        pd.testing.assert_series_equal(original[column].iloc[:201],changed[column].iloc[:201])


def flat_frame():
    f = candles(4)
    f[['open','high','low','close']] = 100.
    f['entry'] = [1,0,0,0]
    f['break_side'] = [0,-1,0,0]
    f['peak_exit'] = 0
    return f


def test_execution_costs_funding_and_closed_trade_reconcile():
    f = flat_frame()
    funding = [dict(fundingTime=MINUTE,fundingRate='.001',markPrice='100')]
    stats, trades, curve = simulate(f,funding,'opposite_break',0,4*MINUTE)
    assert [t['side'] for t in trades] == [1,-1]
    assert trades[0]['exit_time'] == MINUTE
    quantity = 1000/(100*(1+SLIPPAGE_PCT))
    assert stats['funding'] == pytest.approx(-quantity*100*.001)
    assert trades[0]['exit_price'] == pytest.approx(100*(1-SLIPPAGE_PCT))
    assert stats['fees'] > 3.99*1000*TAKER_FEE_RATE
    assert sum(t['net_pnl'] for t in trades) == pytest.approx(stats['net_pnl'])
    assert curve[-1][1] == pytest.approx(stats['net_pnl'])


def test_peak_exit_does_not_automatically_reverse():
    f = flat_frame()
    f['break_side'] = 0
    f.loc[1,'peak_exit'] = 1
    for variant, count in [('peak_exit_trend_entry',1),('peak_reverse',2)]:
        _, trades, _ = simulate(f,[],variant,0,4*MINUTE)
        assert len(trades) == count
        assert trades[0]['reason'] == 'confirmed_pivot'
        assert trades[0]['exit_time'] == MINUTE


def test_cost_stress_worsens_constant_price_trades():
    f = flat_frame()
    base, _, _ = simulate(f,[],'opposite_break',0,4*MINUTE,1.)
    stress, _, _ = simulate(f,[],'opposite_break',0,4*MINUTE,2.)
    assert stress['net_pnl'] < base['net_pnl']
    assert stress['fees'] > base['fees']
