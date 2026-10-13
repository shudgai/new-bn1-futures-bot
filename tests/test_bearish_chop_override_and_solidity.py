import pandas as pd

from core.gates.candle_solidity_gate import CandleSolidityGate
from core.gates.pipeline import EntryGatePipeline, pipeline


def bearish_live_frame(*, ma_death_cross=True):
    rows = []
    for index in range(12):
        rows.append(dict(
            timestamp=1_800_000 + index * 60_000,
            open=100.0, high=100.2, low=99.8, close=100.0,
            atr=1.0, kc_upper=101.0, kc_middle=100.0, kc_lower=99.0,
            ma5=100.2, ma15=100.3, is_closed=True,
        ))
    rows[-2].update(ma5=100.1, ma15=100.0, kc_middle=100.0)
    rows[-1].update(
        open=100.0, high=102.3, low=99.1, close=99.8,
        kc_lower=99.4, kc_middle=99.9,
        ma5=100.0 if ma_death_cross else 99.8,
        ma15=100.1,
        is_closed=False,
    )
    frame = pd.DataFrame(rows)
    frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
    return frame


def test_bearish_death_cross_or_lower_rail_break_overrides_chop_lockout():
    frame = bearish_live_frame()
    quote = 99.2

    assert EntryGatePipeline.has_bearish_chop_breakout_override(frame, quote)
    assert pipeline.market_regime_problem('CAP/USDT', frame, quote) is None
    assert pipeline.chop_lockout_problem(frame, quote) is None

    # The death-cross branch is independent of the lower-band branch.
    frame.loc[frame.index[-1], 'kc_lower'] = 99.0
    assert EntryGatePipeline.has_bearish_chop_breakout_override(frame, quote)


def test_chop_override_does_not_apply_without_bearish_breakout():
    frame = bearish_live_frame(ma_death_cross=False)
    frame.loc[frame.index[-1], ['kc_lower', 'ma5', 'open']] = [98.0, 100.0, 99.0]

    assert not EntryGatePipeline.has_bearish_chop_breakout_override(frame, 99.2)
    previous_provider = pipeline.market_regime_provider
    pipeline.set_market_regime_provider(lambda _symbol: 'CHOPPY')
    try:
        assert pipeline.market_regime_problem('BTC/USDT', frame, 99.2) == 'BLOCKED_BY_AI_CHOP_REGIME'
    finally:
        pipeline.set_market_regime_provider(previous_provider)


def test_live_lower_rail_dump_uses_20_percent_solidity_floor():
    frame = bearish_live_frame()
    quote = 99.2  # 0.2 ATR below the live KC lower rail.
    allowed, _ = CandleSolidityGate.evaluate(frame, quote, 'SHORT')
    assert allowed  # body/range is 25%; normal entries retain the 55% floor.

    frame.loc[frame.index[-1], 'high'] = 103.31  # body/range falls below 20%.
    allowed, reason = CandleSolidityGate.evaluate(frame, quote, 'SHORT')
    assert not allowed
    assert reason == 'BLOCKED_BY_WEAK_BODY'

    # A break of less than 0.15 ATR keeps the ordinary 55% floor.
    frame.loc[frame.index[-1], 'high'] = 102.3
    allowed, _ = CandleSolidityGate.evaluate(frame, 99.3, 'SHORT')
    assert not allowed
