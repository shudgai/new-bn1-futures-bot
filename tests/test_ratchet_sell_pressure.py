import copy
import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,STATE_KEY
from core.services.post_profit_lock_gate import profit_exit_fields
from test_tiered_profit_gate import position


def observe(p,gain,stamp,mode='strong'):
    sign=1 if p['side']=='LONG' else -1
    price=100*(1+sign*gain)
    s=dict(quote_ms=stamp,live_open=price-sign,ma5=price-sign,last_ma5=price-2*sign)
    if mode=='below':s.update(ma5=price+sign,last_ma5=price+2*sign,live_open=price+sign)
    if mode=='turn':s.update(ma5=price-sign,last_ma5=price)
    if mode=='red':s.update(ma5=price-sign,last_ma5=price,live_open=price+sign)
    if mode=='adverse_body':s.update(live_open=price+sign)
    if mode=='missing':s=dict(quote_ms=stamp)
    return evaluate_peak_trailing(p,price,s,fee=0,slippage=0)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_ratchet_arms_without_closing_and_never_retreats(side):
    p=position(side);sign=1 if side=='LONG' else -1
    assert observe(p,.049,61000) is None
    assert not p[STATE_KEY].get('ratchet_armed')
    for stamp,gain in [(62000,.05),(63000,.1),(64000,.2)]:
        assert observe(p,gain,stamp) is None
        assert p[STATE_KEY]['ratchet_armed']
    floor=p[STATE_KEY]['locked_floor_price']
    # A deep retracement alone cannot exit if MA5 remains supportive.
    assert observe(p,.16,65000) is None
    assert p[STATE_KEY]['locked_floor_price']==floor
    assert p[STATE_KEY]['trend_hold_reason']=='RIDING_STRONG_TREND'
    restored=copy.deepcopy(p)
    assert observe(restored,.16,66000) is None
    assert restored[STATE_KEY]['locked_floor_price']==floor

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('mode',['below','turn','red'])
def test_armed_pressure_exits_and_records_gate(side,mode):
    p=position(side)
    observe(p,.06,61000)
    d=observe(p,.04,62000,mode)
    if side == 'LONG':
        if mode == 'below':
            assert d['trigger'] == 'LONG_EXIT_STRUCTURE_DEFENSE'
        else:
            assert d is None
        return
    assert d is None
    assert 'pending' not in p[STATE_KEY]

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_unarmed_pressure_never_exits_or_replays_legacy_ticket(side):
    p=position(side)
    observe(p,.02,61000)
    p[STATE_KEY].update(profit_lock_basis='price_return_v1',pending='PROFIT_LOCK_T1',trigger='PROFIT_LOCK_T1')
    result=observe(p,.01,62000,'below')
    if side == 'LONG':
        assert result['trigger']=='LONG_EXIT_STRUCTURE_DEFENSE'
        assert p[STATE_KEY]['pending']==result['reason']
    else:
        assert result is None
        assert 'pending' not in p[STATE_KEY]
    assert 'tiered_roi_peak' not in p[STATE_KEY]
    assert observe(p,.01,63000,'missing') is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_armed_peak_retracement_with_adverse_body_exits(side):
    p=position(side)
    observe(p,.06,61000)

    result=observe(p,.04,62000,'adverse_body')

    assert result is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_ma5_turn_overrides_healthy_lifeline_hold(side):
    p=position(side)
    sign=1 if side=='LONG' else -1
    observe(p,.06,61000)
    price=100*(1+sign*.055)
    snapshot=dict(quote_ms=62000,live_open=price+sign,ma5=price-sign,
                  last_ma5=price,ma15=price-sign,kc_middle=price-sign)

    result=evaluate_peak_trailing(p,price,snapshot,fee=0,slippage=0)

    assert result is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_locked_floor_cross_alone_does_not_close_during_supportive_trend(side):
    p=position(side)
    observe(p,.20,61000)
    floor=p[STATE_KEY]['locked_floor_price']
    sign=1 if side=='LONG' else -1
    price=100+sign*18.
    snapshot=dict(quote_ms=62000,live_open=price-sign,ma5=price-sign,
                  last_ma5=price-2*sign,ma15=price-2*sign,kc_middle=price-2*sign)

    result=evaluate_peak_trailing(p,price,snapshot,fee=0,slippage=0)

    assert result is None
    assert p[STATE_KEY]['locked_floor_price']==floor


def long_upper_break_snapshot():
    return dict(
        quote_ms=180001., live_bar_ms=180000., closed_bar_ms=120000.,
        live_open=104.9, live_high=105.4, live_low=104.8, atr=1.,
        live_kc_upper=105.3, live_kc_middle=102.8,
        live_ma5=103., closed_ma5=102.8,
        history_outer_pivots=[
            dict(ms=60000., h=103., kc_upper=104., kc_middle=102., ma5=101.),
            dict(ms=120000., h=104., kc_upper=105., kc_middle=102.5, ma5=101.5),
        ],
    )


def test_long_green_candle_is_held_and_records_upper_rail_break():
    p=position('LONG')
    snapshot=long_upper_break_snapshot()

    result=evaluate_peak_trailing(p,105.4,snapshot,fee=0,slippage=0)

    assert result is None
    assert p[STATE_KEY]['peak_price']==pytest.approx(105.4)


def test_long_first_red_after_upper_rail_break_closes_intrabar():
    p=position('LONG')
    snapshot=long_upper_break_snapshot()
    evaluate_peak_trailing(p,105.4,snapshot,fee=0,slippage=0)
    snapshot.update(quote_ms=180002.,live_open=105.3,live_high=105.4)

    result=evaluate_peak_trailing(p,105.0,snapshot,fee=0,slippage=0)

    assert result['trigger']=='LONG_EXIT_OVERBOUGHT_EXTREME_RATCHET'


def test_long_overextended_retracement_uses_point_three_five_atr_threshold():
    p=position('LONG')
    snapshot=long_upper_break_snapshot()
    evaluate_peak_trailing(p,105.4,snapshot,fee=0,slippage=0)
    snapshot.update(quote_ms=180002.,live_open=105.05,live_high=105.4)

    result=evaluate_peak_trailing(p,105.05,snapshot,fee=0,slippage=0)

    assert result['trigger']=='LONG_EXIT_OVERBOUGHT_EXTREME_RATCHET'


def test_long_in_channel_retracement_alone_does_not_close():
    p=position('LONG')
    evaluate_peak_trailing(p,106.,61000,fee=0,slippage=0)
    snapshot=dict(quote_ms=62000.,live_open=104.,live_ma5=102.,
                  kc_middle=101.,atr=1.)

    assert evaluate_peak_trailing(p,103.,snapshot,fee=0,slippage=0) is None


@pytest.mark.parametrize(
    ('snapshot','expected'),
    [
        (dict(quote_ms=62000.,live_open=101.,live_ma5=100.8,kc_middle=99.),
         'LONG_EXIT_STRUCTURE_DEFENSE'),
        (dict(quote_ms=62000.,live_open=101.,live_ma5=99.,kc_middle=100.6),
         'LONG_EXIT_STRUCTURE_DEFENSE'),
    ],
)
def test_long_in_channel_red_body_breaks_only_ma5_or_kc_middle(snapshot,expected):
    result=evaluate_peak_trailing(position('LONG'),100.5,snapshot,fee=0,slippage=0)

    assert result['trigger']==expected


def test_long_green_body_holds_even_below_ma5_and_kc_middle():
    snapshot=dict(quote_ms=62000.,live_open=100.,live_ma5=101.,kc_middle=102.)

    assert evaluate_peak_trailing(position('LONG'),100.5,snapshot,fee=0,slippage=0) is None


def test_symmetric_dual_track_exit_policies():
    from core.services.exits.peak_trailing_exit import evaluate_symmetric_dual_track_exit

    long_args = dict(position_side='LONG', entry_price=100., ticker_price=105.0,
                     candle_open=103., candle_high=105.2, candle_low=102.,
                     kc_upper=105., kc_mid=101., kc_lower=95., ma5=102., atr=1.,
                     session_extreme_price=105.2)
    assert evaluate_symmetric_dual_track_exit(**long_args) is None
    long_args.update(ticker_price=104.8, candle_high=105.2, session_extreme_price=105.2)
    assert evaluate_symmetric_dual_track_exit(**long_args) == 'LONG_EXIT_OVERBOUGHT_EXTREME_RATCHET'
    long_args.update(ticker_price=102.9, candle_open=103.)
    assert evaluate_symmetric_dual_track_exit(**long_args) == 'LONG_EXIT_OVERBOUGHT_EXTREME_RATCHET'
    long_args.update(ticker_price=101.9, candle_high=104., session_extreme_price=104.)
    assert evaluate_symmetric_dual_track_exit(**long_args) == 'LONG_EXIT_STRUCTURE_DEFENSE'

    short_args = dict(position_side='SHORT', entry_price=100., ticker_price=94.4,
                      candle_open=94.5, candle_high=95., candle_low=94.4,
                      kc_upper=105., kc_mid=100., kc_lower=95., ma5=97., atr=1.,
                      session_extreme_price=94.4)
    assert evaluate_symmetric_dual_track_exit(**short_args) is None
    short_args.update(ticker_price=94.8, candle_open=94.7)
    assert evaluate_symmetric_dual_track_exit(**short_args) == 'SHORT_EXIT_OVERSOLD_WICK_REJECTION'
    short_args.update(ticker_price=94.71, candle_open=95.)
    assert evaluate_symmetric_dual_track_exit(**short_args) == 'SHORT_EXIT_OVERSOLD_WICK_REJECTION'
    short_args.update(session_extreme_price=95.2, ticker_price=100.5, candle_open=100.)
    assert evaluate_symmetric_dual_track_exit(**short_args) == 'SHORT_EXIT_MID_BREAKOUT_STOP'


@pytest.mark.parametrize(
    ('side','first_price','first_open','second_price','second_open','upper','middle','lower','expected'),
    [
        ('LONG',105.5,105.4,105.0,105.1,105.,100.,95.,'LONG_EXIT_OVERBOUGHT_EXTREME_RATCHET'),
        ('SHORT',94.4,94.5,94.8,94.7,105.,100.,95.,'SHORT_EXIT_OVERSOLD_WICK_REJECTION'),
    ],
)
def test_peak_trailing_uses_symmetric_dual_track_exit(
    side,first_price,first_open,second_price,second_open,upper,middle,lower,expected
):
    p=position(side)
    p['open_timestamp']=60.
    snapshot=dict(
        quote_ms=180001.,live_bar_ms=180000.,closed_bar_ms=120000.,atr=1.,
        live_open=first_open,live_high=max(first_price,first_open),
        live_low=min(first_price,first_open),live_kc_upper=upper,
        live_kc_middle=middle,live_kc_lower=lower,live_ma5=100.,
    )
    assert evaluate_peak_trailing(p,first_price,snapshot,fee=0,slippage=0) is None
    snapshot.update(quote_ms=180002.,live_open=second_open)

    result=evaluate_peak_trailing(p,second_price,snapshot,fee=0,slippage=0)

    assert result['trigger']==expected


@pytest.mark.parametrize('exit_kind',['ma5','retracement'])
def test_band_walk_long_uses_sensitive_intrabar_defense(exit_kind):
    p=position('LONG')
    p['entry_snapshot']={'signal_code':'KC_BAND_WALK_LONG'}
    snapshot=dict(
        quote_ms=180001.,live_bar_ms=180000.,closed_bar_ms=120000.,atr=1.,
        live_open=104.,live_high=105.,live_low=103.8,
        live_kc_upper=103.,live_kc_middle=101.,live_kc_lower=99.,
        live_ma5=103.5,
    )
    evaluate_peak_trailing(p,105.,snapshot,fee=0,slippage=0)
    snapshot.update(quote_ms=180002.,live_open=104.2,live_high=105.)
    price=103.4 if exit_kind=='ma5' else 104.65

    result=evaluate_peak_trailing(p,price,snapshot,fee=0,slippage=0)

    assert result['trigger']=='BAND_WALK_LONG_RISK_EXIT'


@pytest.mark.parametrize(
    ('stage','peak_return','floor_return','expected'),
    [(1,.025,.017,'TIERED_RATCHET_TP_STAGE_1'),
     (2,.050,.038,'TIERED_RATCHET_TP_STAGE_2'),
     (3,.080,.065,'TIERED_RATCHET_TP_STAGE_3')],
)
@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_tiered_ratchet_floor_is_symmetric_and_persistent(
    stage,peak_return,floor_return,expected,side
):
    from core.services.exits.peak_trailing_exit import evaluate_tiered_ratchet_exit

    sign=1 if side=='LONG' else -1
    peak=100.*(1.+sign*peak_return)
    state={}
    assert evaluate_tiered_ratchet_exit(side,100.,peak,peak,.5,state) is None
    assert state['profit_lock_stage']==stage
    price=100.*(1.+sign*(floor_return-.0001))

    result=evaluate_tiered_ratchet_exit(side,100.,price,peak,.5,state)

    assert result==expected


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_peak_trailing_returns_full_close_for_tiered_ratchet(side):
    p=position(side)
    p['open_timestamp']=60.
    sign=1 if side=='LONG' else -1
    evaluate_peak_trailing(p,102.5 if side=='LONG' else 97.5,61000,
                           fee=0,slippage=0)
    snapshot=dict(quote_ms=62000.,live_open=102.4 if side=='LONG' else 97.6,
                  atr=.5,live_ma5=100.,live_kc_middle=100.,
                  live_kc_upper=105.,live_kc_lower=95.)

    result=evaluate_peak_trailing(
        p,101.69 if side=='LONG' else 98.31,snapshot,fee=0,slippage=0
    )

    assert result['action']=='FULL_CLOSE'
    assert result['trigger']=='TIERED_RATCHET_TP_STAGE_1'
