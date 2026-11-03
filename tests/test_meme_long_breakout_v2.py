"""Owner-approved early LONG slope exception and bounded squeeze breakout."""

import asyncio
from decimal import Decimal, ROUND_DOWN

import pytest

from core.gates.master_gate_funnel import MasterGateFunnel, evaluate_meme_entry_frame
from core.services.entry_methods import MEME_GATE_ENTRY, evaluate_entry_method, revalidate_entry_method
from test_early_midline_entry import early_frame
from test_entry_methods import method_context
from test_meme_master_funnel import market

SYMBOLS = ["龙虾/USDT", "CAP/USDT", "1000PEPE/USDT"]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("body,eligible", [(.999999, False), (1, True), (1.000001, True)])
@pytest.mark.parametrize("slope", [-.1, 0, .1])
def test_early_breakout_half_previous_atr_and_lagging_slope(symbol, body, eligible, slope):
    m = market(symbol=symbol)._replace(
        open_price=101.7 - body, low_price=101.7 - body - .01,
        kc_middle_prev=101 - slope, ma5_prev=101.1,
    )
    assert MasterGateFunnel.is_early_breakout_long(m) is eligible
    decision = MasterGateFunnel.evaluate_open_long(m)
    if eligible:
        assert decision.can_execute
        assert decision.details["trigger"] == "TRIGGER_1145_EARLY_BREAKOUT"
    elif slope < 0:
        assert not decision.can_execute


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("mode", ["early", "squeeze"])
@pytest.mark.parametrize("fault", [
    "macro", "quote_middle", "quote_ma15", "ma_down", "ma_alignment",
    "bias", "chop", "unclosed", "invalid_atr", "cooldown",
])
def test_new_long_triggers_do_not_skip_common_guards(symbol, mode, fault):
    m = market(symbol=symbol)
    if mode == "squeeze":
        m = m._replace(open_price=101.2 - 1.4, low_price=99.79, close_price=101.2,
                       quote=101.2, kc_upper=101.1, ma15_curr=100.9, ma5_curr=101,
                       ma5_prev=100.99)
    if fault == "macro":
        m = m._replace(macro_trend="BEARISH")
    elif fault == "quote_middle":
        m = m._replace(quote=m.kc_middle)
    elif fault == "quote_ma15":
        m = m._replace(quote=m.ma15_curr)
    elif fault == "ma_down":
        m = m._replace(ma5_prev=m.ma5_curr + .01)
    elif fault == "ma_alignment":
        m = m._replace(ma5_curr=m.ma15_curr - .01)
    elif fault == "bias":
        m = m._replace(quote=m.ma15_curr + m.atr + .001)
    elif fault == "chop":
        m = m._replace(ma_cross_count_8=3)
    elif fault == "unclosed":
        m = m._replace(is_closed=False)
    elif fault == "invalid_atr":
        m = m._replace(atr=float("nan"))
    decision = MasterGateFunnel.evaluate_open_long(m, fault == "cooldown")
    if fault == "cooldown":
        # Priority channels (bottom pivot cross / big-body breakout) exempt the cooldown.
        assert decision.can_execute and decision.details.get("priority_channel")
    else:
        assert not decision.can_execute


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("body,eligible", [(1.399999, False), (1.4, True), (1.400001, True)])
def test_squeeze_threshold_requires_closed_green_outside_upper(symbol, body, eligible):
    m = market(symbol=symbol)._replace(
        close_price=101.2, quote=101.2, open_price=101.2 - body,
        low_price=101.2 - body - .01, kc_upper=101.1,
        ma15_curr=101, ma5_curr=101.1,
    )
    assert MasterGateFunnel.is_squeeze_breakout_long(m) is eligible


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_squeeze_alone_cannot_override_adverse_kc_slope(symbol):
    frame = long_frame("squeeze")
    last, prev = frame.index[-1], frame.index[-2]
    frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"] + .1
    decision = evaluate_meme_entry_frame(frame, symbol, "LONG", frame.iloc[-1]["close"])
    # Priority channels exempt the lagging KC middle slope by spec.
    assert decision.can_execute and decision.details.get("priority_channel")


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("mode", ["early", "squeeze"])
def test_reference_atr_not_signal_candle_atr_controls_new_thresholds(symbol, mode):
    frame = long_frame(mode)
    frame.loc[frame.index[-1], "atr"] = 100
    signal = evaluate_entry_method(
        frame, symbol=symbol, quote=frame.iloc[-1]["close"], expected_method=MEME_GATE_ENTRY,
    )
    assert signal is not None
    assert signal["entry_atr"] == frame.iloc[-2]["atr"] == 2
    frame.loc[frame.index[-2], "atr"] = 10
    assert evaluate_entry_method(
        frame, symbol=symbol, quote=frame.iloc[-1]["close"], expected_method=MEME_GATE_ENTRY,
    ) is None


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("offset,allowed", [(-.000001, True), (0, True), (.000001, False)])
def test_squeeze_limit_uses_more_extreme_quote_and_close(symbol, offset, allowed):
    m = market(symbol=symbol)._replace(
        atr=2, kc_middle=100, kc_middle_prev=100, kc_lower=99,
        kc_upper=100.1, ma15_curr=101.15, ma15_prev=101.15,
        ma5_curr=101.2, ma5_prev=101.19,
        open_price=101.2, close_price=102.6, low_price=101.19,
        high_price=102.61, quote=103.1 + offset,
    )
    decision = MasterGateFunnel.evaluate_open_long(m)
    assert decision.can_execute is allowed, decision
    if allowed:
        assert decision.details["trigger"] == "TRIGGER_1148_SQUEEZE_BREAKOUT"
    else:
        assert decision.reason == "BLOCKED_LONG_KC_BAND_EXTENSION"


def long_frame(mode):
    frame = early_frame("LONG")
    last, prev = frame.index[-1], frame.index[-2]
    frame.loc[last, "atr"] = 2
    if mode == "early":
        frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"] + .1
    else:
        frame.loc[last, ["open", "low", "kc_upper", "kc_middle", "ma15", "ma5"]] = [
            100.1, 100.09, 101.1, 99.8, 99.9, 100,
        ]
        frame.loc[prev, ["ma5", "ma15", "kc_middle"]] = [99.99, 99.9, 99.7]
        frame.loc[last, "close"] = 101.5
    return frame


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("mode", ["early", "squeeze"])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("fault", [None, "quote_extension", "trigger_lost", "macro", "cooldown"])
def test_new_authorities_scan_revalidate_and_submit_or_veto(
    symbol, mode, account_type, fault, monkeypatch, tmp_path,
):
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from core.services.execution_manager import ExecutionManager
    from core.services.data_pipeline import DataPipeline
    from test_testnet_account import FakeTestnetExchange
    import time

    frame = long_frame(mode)
    quote = float(frame.iloc[-1]["close"])
    signal = evaluate_entry_method(frame, quote=quote, symbol=symbol, expected_method=MEME_GATE_ENTRY)
    assert signal is not None
    trigger = "TRIGGER_1145_EARLY_BREAKOUT" if mode == "early" else "TRIGGER_1148_SQUEEZE_BREAKOUT"
    assert signal["trigger"] == trigger
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    for key in ("_last_exit_record", "_last_trade_bar_time", "_hourly_entry_records"):
        monkeypatch.setattr(ExecutionManager, key, {})
    if account_type == "paper":
        account = PaperAccount()
    else:
        monkeypatch.setattr(BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))
        exchange = FakeTestnetExchange()
        exchange.amount_to_precision = lambda _symbol, amount: str(
            Decimal(str(amount)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        )
        account = BinanceTestnetAccount(exchange, state_file=str(tmp_path / "testnet.json"))
        asyncio.run(account.initialize())
    account.balance = account.available_balance = 150
    fresh = frame.copy()
    if fault == "quote_extension":
        fresh.loc[fresh.index[-1], "ma15"] = quote - 2.01
    elif fault == "trigger_lost":
        fresh.loc[fresh.index[-1], "open"] = quote - .81
    elif fault == "macro":
        fresh.attrs["macro_trend"] = "BEARISH"
    elif fault == "cooldown":
        account.last_closed_at[symbol] = time.time() - 90
    account.entry_frame_provider = lambda *_a, **_k: fresh
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, quote)
    assert snapshot is not None
    snapshot.raw_df = frame
    snapshot.macro_trend = "BULLISH"
    snapshot.curr_k, snapshot.prev_k = frame.iloc[-1].to_dict(), frame.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snapshot))
    result = asyncio.run(ExecutionManager.process_symbol_pipeline(symbol, account, klines_df=frame, quote=quote))
    if fault:
        assert result["status"] != "ENTRY_EXECUTED", result
        assert symbol not in account.positions
        assert not account.trades
    else:
        assert result["status"] == "ENTRY_EXECUTED", str(result) + str(account.logs[-10:])
        assert account.positions[symbol]["entry_method_authority"] == MEME_GATE_ENTRY
        assert revalidate_entry_method(frame, symbol=symbol, side="LONG", quote=quote, context=method_context(signal))[0]
