import asyncio
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolated_account_imports(monkeypatch, tmp_path):
    import core.config as config
    import core.paper_account as paper

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(paper, "STATE_FILE", str(tmp_path / "paper.json"))


@pytest.fixture
def manual_service(monkeypatch):
    from core.services import manual_order_service as service

    frame = pd.DataFrame([
        {
            "timestamp": 60_000 * (index + 1), "open": 100 + index * 0.1,
            "close": 100.05 + index * 0.1, "high": 100.2 + index * 0.1,
            "low": 99.9 + index * 0.1, "ma3": 99.9 + index * 0.1,
            "ma5": 99.8 + index * 0.1, "ma15": 99.5 + index * 0.1,
            "kc_middle": 99.5 + index * 0.1,
            "kc_upper": 102 + index * 0.1, "kc_lower": 97 + index * 0.1,
            "atr": 1.0, "volume": 100.0, "is_closed": index < 24,
        }
        for index in range(25)
    ])
    frame.attrs["timeframe_ms"] = 60_000
    account = SimpleNamespace(
        positions={}, get_available_balance=lambda: 1000,
        get_wallet_balance=lambda: 1000, open_position=AsyncMock(return_value=True),
    )
    engine = SimpleNamespace(
        account=account, tickers={"龙虾/USDT": 102.45},
        data_frames={"龙虾/USDT": frame},
        _full_wallet_entry_margin=lambda *args: 100,
        release_manual_close_state=Mock(), _take_over_manual_position=Mock(),
    )
    monkeypatch.setattr(service, "engine", engine)
    monkeypatch.setattr(service, "_manual_order_lock", asyncio.Lock())
    return service, engine, frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_manual_order_preserves_original_finality_without_intrabar_bypass(
    manual_service, monkeypatch, side,
):
    from core.services import math_execution_gate

    service, engine, frame = manual_service
    validate = AsyncMock(return_value=(True, "PASS"))
    monkeypatch.setattr(math_execution_gate, "validate_entry_at_execution", validate)
    result = asyncio.run(service.process_manual_order("龙虾/USDT", side))
    assert result["success"]
    validate.assert_awaited_once()
    engine.account.open_position.assert_awaited_once()
    context = engine.account.open_position.call_args.kwargs["entry_context"]
    assert context["entry_gate_frame"] is frame
    assert not frame.iloc[-1]["is_closed"]
    assert not context.get("intrabar_authorized")
    assert not context.get("direction_override")
    assert validate.call_args.kwargs["execution_price"] == 102.45
    assert context is validate.call_args.args[3]


def test_manual_order_returns_actual_gate_reason_without_submission(
    manual_service, monkeypatch,
):
    from core.services import math_execution_gate

    service, engine, _ = manual_service
    monkeypatch.setattr(
        math_execution_gate, "validate_entry_at_execution",
        AsyncMock(return_value=(False, "BLOCKED_STALE_DATA")),
    )
    result = asyncio.run(service.process_manual_order("龙虾/USDT", "LONG"))
    assert not result["success"]
    assert "BLOCKED_STALE_DATA" in result["detail"]
    engine.account.open_position.assert_not_awaited()


@pytest.mark.parametrize("finality", ["missing", "all_live", "non_contiguous"])
def test_manual_order_rejects_unverified_finality(manual_service, finality):
    service, engine, frame = manual_service
    if finality == "missing":
        frame.drop(columns=["is_closed"], inplace=True)
    elif finality == "all_live":
        frame["is_closed"] = False
    else:
        frame.loc[10, "is_closed"] = False
    result = asyncio.run(service.process_manual_order("龙虾/USDT", "LONG"))
    assert not result["success"]
    assert "開倉條件未通過" in result["detail"]
    engine.account.open_position.assert_not_awaited()


@pytest.mark.parametrize(
    "side,reason",
    [
        ("LONG", "VETO_INTRABAR_NOT_CLOSED"),
        ("SHORT", "BLOCKED_DIRECTION_LOCK_SHORT_KC_SLOPE_NOT_DOWN"),
    ],
)
def test_manual_order_preflight_keeps_real_structure_gate(manual_service, side, reason):
    service, engine, _ = manual_service
    result = asyncio.run(service.process_manual_order("龙虾/USDT", side))
    assert not result["success"]
    assert "開倉條件未通過" in result["detail"]
    assert reason in result["detail"]
    engine.account.open_position.assert_not_awaited()


def test_manual_order_final_rejection_is_not_reported_as_a_fill(
    manual_service, monkeypatch,
):
    from core.services import math_execution_gate

    service, engine, _ = manual_service
    monkeypatch.setattr(
        math_execution_gate, "validate_entry_at_execution",
        AsyncMock(return_value=(True, "PASS")),
    )
    engine.account.open_position.return_value = False
    result = asyncio.run(service.process_manual_order("龙虾/USDT", "LONG"))
    assert not result["success"]
    assert "未確認成功" in result["detail"]
    engine.account.open_position.assert_awaited_once()
    engine.release_manual_close_state.assert_not_called()


def test_reset_rejects_exchange_account_without_mutation(monkeypatch):
    from fastapi import HTTPException
    from services import api

    account = SimpleNamespace(positions={"TEST": {"qty": 1}}, balance=100)
    monkeypatch.setattr(api.engine, "account", account)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.reset_account())
    assert exc.value.status_code == 501
    assert "交易所" in exc.value.detail
    assert account.positions == {"TEST": {"qty": 1}}
    assert account.balance == 100


def test_reset_supported_account_calls_existing_reset_once(monkeypatch):
    from services import api

    account = SimpleNamespace(reset_state=Mock())
    monkeypatch.setattr(api.engine, "account", account)
    assert asyncio.run(api.reset_account())["status"] == "success"
    account.reset_state.assert_called_once_with()


@pytest.mark.parametrize("supports_reset", [True, False])
def test_status_exposes_account_reset_capability(monkeypatch, supports_reset):
    import core.paper_account as paper
    from fastapi import Response
    from services import api

    account = paper.PaperAccount()
    if not supports_reset:
        account.reset_state = None
    monkeypatch.setattr(api.engine, "account", account)
    response = asyncio.run(api.get_status(Response()))
    assert json.loads(response.body)["reset_account_supported"] is supports_reset


def test_ui_operations_handle_unsupported_reset_rejection_network_and_timeout():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web/index.html").read_text()
    assert ':disabled="isResetting || !canResetAccount"' in html
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert len(scripts) == 1
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
let config;
const alerts = [];
let confirms = 0, calls = 0, clears = 0;
const sandbox = {
    Vue: {createApp(value) { config = value; return {mount() {}}; }},
    localStorage: {getItem() {return null;}},
    AbortController, console: {error() {}},
    window: {alert(value) {alerts.push(value);}, confirm() {confirms++;return true;}},
    setTimeout() {return 1;}, clearTimeout() {clears++;},
};
vm.runInNewContext(SOURCE, sandbox);
async function main() {
    const app = {...config.data(), ...config.methods};
    app.fetchStatus = async () => {};
    for (const bot of [
        {paper_trading:false, environment:'binance_demo_trading'},
        {paper_trading:false, environment:'binance_live'},
        {paper_trading:true, reset_account_supported:false},
    ]) {
        app.bot = bot;
        app.canResetAccount = config.computed.canResetAccount.call(app);
        assert.equal(app.canResetAccount, false);
        await app.resetAccount();
    }
    assert.equal(confirms, 0);
    app.bot = {paper_trading:true};
    assert.equal(config.computed.canResetAccount.call(app), true);
    sandbox.fetch = async () => {
        calls++;
        return {ok:false,status:400,json:async()=>({detail:'BLOCKED_STALE_DATA'})};
    };
    await app.placeOrder('TEST', 'LONG');
    assert.equal(app.isPlacingOrder, false);
    assert.equal(alerts.at(-1), 'BLOCKED_STALE_DATA');
    assert.equal(calls, 1);
    sandbox.fetch = async () => {calls++;throw new Error('offline');};
    await app.placeOrder('TEST','LONG');
    assert.equal(app.isPlacingOrder, false);
    assert.match(alerts.at(-1), /無法連線/);
    assert.equal(calls, 2);
    sandbox.setTimeout = callback => {queueMicrotask(callback);return 1;};
    sandbox.fetch = async (_url, options) => {
        calls++;
        return new Promise((_resolve,reject) => {
            options.signal.addEventListener('abort', () => {
                const error = new Error('timeout');error.name='AbortError';reject(error);
            });
        });
    };
    await app.placeOrder('TEST','LONG');
    assert.equal(app.isPlacingOrder, false);
    assert.match(alerts.at(-1), /操作逾時/);
    assert.match(alerts.at(-1), /勿重複送單/);
    assert.equal(calls, 3);
    assert.equal(clears, 3);
    app.canResetAccount = true;
    sandbox.fetch = async () => {
        calls++;
        return {ok:false,status:501,json:async()=>({detail:'EXCHANGE_RESET_UNSUPPORTED'})};
    };
    await app.resetAccount();
    assert.equal(app.isResetting, false);
    assert.equal(alerts.at(-1), 'EXCHANGE_RESET_UNSUPPORTED');
    assert.equal(calls, 4);
    sandbox.fetch = async () => {
        calls++;
        return {ok:true,json:async()=>({message:'RESET_OK'})};
    };
    await app.resetAccount();
    assert.equal(app.isResetting, false);
    assert.match(alerts.at(-1), /RESET_OK$/);
    assert.equal(calls, 5);
}
main().catch(error => {console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", "const SOURCE=" + json.dumps(scripts[0]) + ";" + harness],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
