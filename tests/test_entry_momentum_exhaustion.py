import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2, evaluate_v2_frame
from test_entry_gate_consistency import frame
from test_missed_breakout_continuation import continuation_frame


def check(body=.5, base=1., atr=1., wick=0., side='LONG', tail=0., third=False):
    rows=[dict(timestamp=60000,open=99.,close=100.,kc_upper=101.,kc_lower=90.,atr=atr),
          dict(timestamp=120000,open=101.1-base,close=101.1,kc_upper=101.,kc_lower=90.,atr=atr)]
    if third:rows.append(dict(rows[-1],timestamp=180000,open=101.,close=101.1))
    live=dict(timestamp=rows[-1]['timestamp']+60000,open=102.,close=102.+body,high=102.+body+wick,low=102.-tail)
    if side=='SHORT':
        for b in rows:
            b['open'],b['close']=300-b['open'],300-b['close']
            b['kc_upper'],b['kc_lower']=300-b['kc_lower'],300-b['kc_upper']
        live.update(open=198.,close=198.-body,high=198.+tail,low=198.-body-wick)
    return PureTrendStrategyV2.advancing_body_rejection(pd.DataFrame(rows),live,side)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('third',[False,True])
def test_half_ignition_boundary_uses_original_body(side,third):
    assert check(body=.499,base=1.,atr=.1,side=side,third=third) and '實體萎縮' in check(body=.499,base=1.,atr=.1,side=side,third=third)
    assert check(body=.5,base=1.,atr=.1,side=side,third=third) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_half_atr_boundary(side):
    assert '0.5 ATR' in check(body=.499,base=.1,atr=1.,side=side)
    assert check(body=.5,base=.1,atr=1.,side=side) is None








