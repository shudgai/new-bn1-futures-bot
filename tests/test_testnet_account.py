import pytest
import ccxt.async_support as ccxt

import core.testnet_account as testnet_module
from core.testnet_account import BinanceTestnetAccount


async def asyncio_sleep_stub(*_args, **_kwargs):
    return None


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeTestnetExchange:
    def __init__(self):
        self.positions = []
        self.orders = []
        self.cancelled = []
        self.leverage = None

    async def load_markets(self):
        return {}

    async def fapiPrivateV2GetBalance(self):
        return [{"asset": "USDT", "balance": "1000", "availableBalance": "900"}]

    async def fapiPrivateV2GetPositionRisk(self):
        return list(self.positions)

    def amount_to_precision(self, symbol, amount):
        return str(round(amount, 3))

    def price_to_precision(self, symbol, price):
        return str(round(price, 4))

    async def set_leverage(self, leverage, symbol):
        self.leverage = (symbol, leverage)

    async def create_order(self, symbol, order_type, side, qty, price=None, params=None):
        order = {
            "id": str(len(self.orders) + 1),
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": qty,
            "params": params or {},
        }
        self.orders.append(order)
        if str(order_type).lower() == "market" and not (params or {}).get("reduceOnly"):
            signed_qty = qty if side == "buy" else -qty
            self.positions = [{
                "symbol": symbol.replace("/", ""),
                "positionAmt": str(signed_qty),
                "entryPrice": "100",
                "markPrice": "100",
                "leverage": str(self.leverage[1]),
                "unRealizedProfit": "0",
            }]
            order["average"] = 100.0
        elif str(order_type).lower() == "market" and (params or {}).get("reduceOnly"):
            if self.positions:
                pos = dict(self.positions[0])
                current_amt = float(pos["positionAmt"])
                reduce_amt = qty if side == "sell" else -qty
                new_amt = current_amt - reduce_amt
                if abs(new_amt) < 1e-4:
                    self.positions = []
                else:
                    pos["positionAmt"] = str(new_amt)
                    self.positions = [pos]
            else:
                self.positions = []
            order["average"] = 101.0
        return order

    async def request(self, path, api, method, params):
        if method == "DELETE":
            self.cancelled.append(params["symbol"])
            return {}
        order = {
            "id": str(len(self.orders) + 1),
            "symbol": params["symbol"],
            "type": params["type"],
            "side": params["side"].lower(),
            "amount": float(params["quantity"]),
            "params": params,
        }
        self.orders.append(order)
        return {"algoId": order["id"]}

    async def cancel_all_orders(self, symbol):
        self.cancelled.append(symbol)

























@pytest.mark.anyio
async def test_place_limit_entry_rejects_zero_amount(tmp_path, monkeypatch):
    """amount_usdt<=0 時要提早拒絕、印出警告，不能讓 qty=0 一路送進
    exchange.amount_to_precision() 炸出未捕捉的交易所例外——實測
    DOGE/USDT 07/29 這筆就是 MIN_SCORE_THRESHOLD 跟 POSITION_SIZE_TIERS
    最低檔沒對齊，算出 amount_usdt=0，把整個主迴圈拖垮反覆重炸。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    placed = await account.place_limit_entry(
        "DOGE/USDT", "LONG", 100.0, amount_usdt=0.0, sl=98.0, tp=103.0,
        reason="test", atr=1.0, leverage=5, signal_score=100,
    )

    assert placed is False
    assert len(exchange.orders) == 0
    assert any("金額為 0" in entry["text"] for entry in account.logs)




@pytest.mark.anyio
async def test_refresh_flags_profit_alert_when_giveback_from_peak(tmp_path, monkeypatch):
    """獲利了結參考提醒：目前還有獲利，但從至今最高浮盈回吐超過
    PROFIT_ALERT_GIVEBACK_RATIO，就標記 profit_alert=True。純顯示用，
    不影響三階段自動移動停利（那套維持原樣，這裡另外疊加一個提醒）。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    # 進場 100，目前標記價 105（浮盈 5%），但歷史最高浮盈曾到 10%，
    # 回吐 = (10%-5%)/10% = 50% >= 20% 門檻，應該觸發提醒。
    account.position_meta["DOGE/USDT"] = {"highest_pnl_pct": 0.10}
    exchange.positions = [{
        "symbol": "DOGEUSDT", "positionAmt": "100", "entryPrice": "100",
        "markPrice": "105", "leverage": "5", "unRealizedProfit": "50",
    }]

    await account.refresh(force=True)

    pos = account.positions["DOGE/USDT"]
    assert pos["profit_alert"] is True
    assert pos["peak_pnl_pct"] == pytest.approx(0.10)




@pytest.mark.anyio
async def test_refresh_no_profit_alert_when_still_near_peak(tmp_path, monkeypatch):
    """浮盈還貼在高點附近（回吐幅度小於門檻），不誤報。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    # 浮盈 4.5%，歷史最高 5%，回吐僅 10% < 20% 門檻。
    account.position_meta["DOGE/USDT"] = {"highest_pnl_pct": 0.05}
    exchange.positions = [{
        "symbol": "DOGEUSDT", "positionAmt": "100", "entryPrice": "100",
        "markPrice": "104.5", "leverage": "5", "unRealizedProfit": "45",
    }]

    await account.refresh(force=True)

    pos = account.positions["DOGE/USDT"]
    assert pos["profit_alert"] is False



@pytest.mark.skip(reason="early profit guard removed")
@pytest.mark.anyio
async def test_testnet_early_profit_guard_closes_on_giveback(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "ENABLE_TRAILING_STOP", True)
    monkeypatch.setattr(testnet_module, "USE_NATIVE_TRAILING_STOP", False)
    monkeypatch.setattr(testnet_module, "TRAILING_TRIGGER_PCT", 1.0)
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()
    account.position_meta["DOGE/USDT"] = {
        "sl": 98.0, "tp": 105.0, "atr": 1.0, "highest_pnl_pct": 0.0,
    }
    exchange.positions = [{
        "symbol": "DOGEUSDT", "positionAmt": "10", "entryPrice": "100.0",
        "markPrice": "100.31", "leverage": "5", "unRealizedProfit": "3.1",
    }]
    account.last_sync_at = 0
    await account.update_positions({"DOGE/USDT": 100.31})
    assert account.position_meta["DOGE/USDT"]["early_profit_guard_armed"] is True
    assert account.position_meta["DOGE/USDT"].get("is_breakeven_moved") is not True

    exchange.positions[0]["markPrice"] = "100.20"
    exchange.positions[0]["unRealizedProfit"] = "2.0"
    account.last_sync_at = 0
    await account.update_positions({"DOGE/USDT": 100.20})

    assert "DOGE/USDT" not in account.positions
    assert account.trades[0]["reason"] == "早期獲利保護回吐平倉"




@pytest.mark.skip(reason="early profit guard removed")
@pytest.mark.anyio
async def test_small_atr_profit_waits_instead_of_arming_loss_making_breakeven(
    tmp_path, monkeypatch
):
    """US/USDT型案例：0.28%毛利雖已超過1.2 ATR，仍不足以覆蓋
    雙邊費用與市價滑點安全帶，不得啟動早期保護或把SL推進淨虧區。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "ENABLE_TRAILING_STOP", True)
    monkeypatch.setattr(testnet_module, "USE_NATIVE_TRAILING_STOP", True)
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()
    account.position_meta["US/USDT"] = {
        "sl": 101.0, "tp": 95.0, "atr": 0.10, "highest_pnl_pct": 0.0,
    }
    exchange.positions = [{
        "symbol": "USUSDT", "positionAmt": "-10", "entryPrice": "100.0",
        "markPrice": "99.72", "leverage": "2", "unRealizedProfit": "2.8",
    }]
    account.last_sync_at = 0
    previous_order_count = len(exchange.orders)

    await account.update_positions({"US/USDT": 99.72})

    meta = account.position_meta["US/USDT"]
    assert meta.get("early_profit_guard_armed") is not True
    assert meta.get("is_breakeven_moved") is not True
    assert meta["sl"] == pytest.approx(101.0)
    assert not any(order["type"] == "STOP_MARKET" for order in exchange.orders[previous_order_count:])






@pytest.mark.anyio
async def test_external_close_classifies_exchange_tp_and_price_fallback_sl(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    account = BinanceTestnetAccount(FakeTestnetExchange())
    position = {
        "side": "LONG", "entry_price": 100.0, "sl": 98.0, "tp": 104.0,
    }

    exit_type, reason = await account._classify_external_close(
        "DOGE/USDT", position,
        [{"type": "TAKE_PROFIT_MARKET", "price": 104.0, "amount": 1.0}],
        104.0,
    )
    assert exit_type == "TP"
    assert "Take-Profit" in reason

    exit_type, reason = await account._classify_external_close(
        "DOGE/USDT", position, [], 97.9,
    )
    assert exit_type == "SL"
    assert "Stop-Loss" in reason


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("position", "close_price"),
    [
        ({"side": "LONG", "entry_price": 100.0, "sl": 100.5, "tp": 104.0}, 100.4),
        ({"side": "SHORT", "entry_price": 100.0, "sl": 99.5, "tp": 96.0}, 99.6),
    ],
)
async def test_external_stop_on_favorable_side_is_profit_protection(
    tmp_path, monkeypatch, position, close_price
):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    account = BinanceTestnetAccount(FakeTestnetExchange())

    exit_type, reason = await account._classify_external_close(
        "DOGE/USDT", position,
        [{"type": "STOP", "price": close_price, "amount": 1.0}],
        close_price,
    )

    assert exit_type == "PROFIT_PROTECT"
    assert "Profit-Protect" in reason





@pytest.mark.anyio
async def test_native_trailing_stop_uses_algo_order_endpoint(tmp_path, monkeypatch):
    """Binance 已把 TRAILING_STOP_MARKET 遷移到 Algo Order API。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))

    class RecordingExchange(FakeTestnetExchange):
        def __init__(self):
            super().__init__()
            self.request_calls = []

        async def request(self, path, api, method, params):
            self.request_calls.append((path, api, method, dict(params)))
            return await super().request(path, api, method, params)

    exchange = RecordingExchange()
    account = BinanceTestnetAccount(exchange)

    result = await account._place_native_trailing_stop(
        "DOGE/USDT",
        "sell",
        10.0,
        atr_pct=0.01,
        tier=3,
        highest_pnl=0.06,
        activation_price=106.0,
    )

    path, api, method, params = exchange.request_calls[-1]
    assert (path, api, method) == ("algoOrder", "fapiPrivate", "POST")
    assert params["algoType"] == "CONDITIONAL"
    assert params["type"] == "TRAILING_STOP_MARKET"
    assert params["activationPrice"] == "106.0"
    assert result["callbackRate"] == params["callbackRate"]




@pytest.mark.anyio
async def test_testnet_market_entry_rejects_existing_pending_order(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "pending-conflict.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()
    account.pending_limit_orders["DOGE/USDT"] = {"side": "LONG"}

    opened = await account.open_position(
        "DOGE/USDT", "LONG", 100.0, 50.0, 95.0, 105.0, "conflict",
        leverage=5, signal_score=80,
    )

    assert opened is False
    assert account.positions == {}
    assert exchange.orders == []




