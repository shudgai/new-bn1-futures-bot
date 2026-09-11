"""Causality and accounting checks for offline research, without order execution."""
import numpy as np
import pandas as pd
from tools.channel_entry_risk_research import (
    indicators, snapshot_at_open, sample_features, partition, filter_metrics,
)


def history():
    close = 100 + np.arange(320)*.02 + np.sin(np.arange(320))*.1
    return pd.DataFrame(dict(timestamp=np.arange(320)*60000, open=close-.03,
        close=close, high=close+.1, low=close-.15, volume=np.arange(320)+100.))


def test_live_and_future_ohlcv_cannot_change_known_features():
    raw = history()
    original = indicators(raw)
    changed = raw.copy()
    changed.loc[250:, ['high', 'close', 'volume']] *= 3
    changed.loc[250:, 'low'] *= .5
    changed.loc[251:, 'open'] *= 2
    altered = indicators(changed)
    keys = ['open', 'high', 'low', 'close', 'volume', 'atr', 'tr',
            'ema_20', 'ma3', 'ma15', 'kc_upper', 'kc_lower']
    pd.testing.assert_frame_equal(snapshot_at_open(original,250)[keys],
                                  snapshot_at_open(altered,250)[keys])
    for side in ('LONG', 'SHORT'):
        assert sample_features(original,250,side,'1000PEPEUSDT') == sample_features(
            altered,250,side,'1000PEPEUSDT')


def test_open_snapshot_matches_causal_recomputation():
    raw = history()
    prefix = raw.iloc[:251].copy()
    prefix.loc[250, ['high', 'low', 'close']] = prefix.loc[250,'open']
    prefix.loc[250,'volume'] = 0.
    expected = indicators(prefix)
    actual = snapshot_at_open(indicators(raw),250)
    for key in ('atr','ema_20','ma3','ma15','kc_upper','kc_lower'):
        assert np.isclose(actual.iloc[-1][key],expected.iloc[-1][key],rtol=1e-12)


def test_time_partitions_purge_future_labels():
    f = pd.DataFrame(dict(timestamp=np.arange(100)*60000,
                          label_end=(np.arange(100)+3)*60000))
    train,cal,test,cuts = partition(f,0,100*60000)
    assert train.label_end.max() < cuts['train_end']
    assert cal.timestamp.min() >= cuts['train_end']
    assert cal.label_end.max() < cuts['calibration_end']
    assert test.timestamp.min() >= cuts['calibration_end']
    assert test.label_end.max() <= cuts['end']
    assert set(train.index).isdisjoint(cal.index)
    assert set(cal.index).isdisjoint(test.index)


def test_missed_positive_proxy_is_not_same_as_nonbad():
    frame = pd.DataFrame(dict(bad_1m=[1,1,0,0],net_1m_bps=[2,-2,3,-3]))
    result = filter_metrics(frame,[True,False,True,False])
    assert result['bad_blocked'] == 1
    assert result['nonbad_miss_rate'] == .5
    assert result['profitable_proxy_miss_rate'] == 1.
    assert result['kept_bad_rate'] == .5
