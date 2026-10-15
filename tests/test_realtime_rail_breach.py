import time
from unittest.mock import patch

import pandas as pd
import pytest

from core.services.entry_contract import (
    REALTIME_RAIL_BREACH_SHORT_CODE,
    evaluate_entry_contract,
    evaluate_realtime_short_rail_breach,
    realtime_short_reward_risk,
)


def bearish_breach_frame():
    start = int(time.time() // 60) * 60_000 - 3 * 60_000
    rows = []
    for index in range(3):
        rows.append(dict(
            timestamp=start + index * 60_000,
            open=100.0, high=100.1, low=99.4, close=99.6,
            kc_upper=101.0, kc_middle=100.4 - index * 0.05,
            kc_lower=99.1, atr=1.0,
            ma3=99.4, ma5=99.5, ma15=99.8,
            is_closed=True,
        ))
    rows.append(dict(
        timestamp=start + 3 * 60_000,
        open=99.8, high=100.0, low=98.7, close=98.8,
        kc_upper=101.0, kc_middle=100.25, kc_lower=99.1,
        atr=1.0, ma3=99.0, ma5=99.2, ma15=99.6,
        is_closed=False,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
    return frame




def test_non_meme_realtime_short_helper_remains_available():
    frame = bearish_breach_frame()
    with patch('core.services.entry_room_service.entry_room', return_value={'target': 95.0}):
        candidate = evaluate_realtime_short_rail_breach(
            frame, 98.8, symbol='TEST/USDT',
        )

    assert candidate['action'] == 'ENTER'
    assert candidate['side'] == 'SHORT'
    assert candidate['type'] == REALTIME_RAIL_BREACH_SHORT_CODE






def test_realtime_short_net_reward_risk_uses_structural_target_and_1_5_atr_stop():
    frame = bearish_breach_frame()
    with patch('core.services.entry_room_service.entry_room', return_value={'target': 96.0}):
        estimate = realtime_short_reward_risk(frame, 98.8, 1.0)

    assert estimate['target'] == 96.0
    assert estimate['stop'] == pytest.approx(100.3)
    assert estimate['reward'] > estimate['risk']
    assert estimate['reward_risk'] > 1.0








