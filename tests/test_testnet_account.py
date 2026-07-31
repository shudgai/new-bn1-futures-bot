import pytest

import core.testnet_account as testnet_module
from core.testnet_account import BinanceTestnetAccount


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
async def test_testnet_account_places_entry_stop_and_take_profit(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "DISABLE_TAKE_PROFIT", False)
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)

    await account.initialize()
    success = await account.open_position(
        "DOGE/USDT",
        "LONG",
        100.0,
        50.0,
        98.0,
        103.0,
        "Fast_Keltner_SuperTrend",
        atr=1.0,
        leverage=5,
        signal_score=100,
    )

    assert success is True
    assert account.balance == 1000.0
    assert account.available_balance == 900.0
    assert "DOGE/USDT" in account.positions
    assert [order["type"] for order in exchange.orders] == [
        "market",
        "STOP",
        "TAKE_PROFIT_MARKET",
    ]
    assert exchange.orders[1]["params"]["reduceOnly"] == "true"
    assert exchange.orders[2]["params"]["reduceOnly"] == "true"


@pytest.mark.anyio
async def test_testnet_account_manual_close_is_reduce_only(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()
    await account.open_position(
        "DOGE/USDT", "LONG", 100.0, 50.0, 98.0, 103.0, "test", leverage=5
    )

    success = await account.close_position("DOGE/USDT", 101.0, "手動平倉")

    assert success is True
    assert "DOGE/USDT" not in account.positions
    assert exchange.orders[-1]["params"]["reduceOnly"] is True
    assert "DOGE/USDT" in exchange.cancelled


@pytest.mark.anyio
async def test_partial_fill_limit_entry_records_actual_margin(tmp_path, monkeypatch):
    """限價單只部分成交時，開倉交易紀錄的「金額」要用實際成交的
    qty×成交價÷槓桿反推，不能直接沿用原本預算的 amount_usdt——否則開倉/
    平倉兩筆紀錄的金額對不上，讓人誤以為部位沒平乾淨（實測 SUI/USDT
    07/29 這筆就是這樣，開倉記成 30U、平倉卻反推出 23.88U）。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )

    class PartialFillExchange(FakeTestnetExchange):
        async def create_order(self, symbol, order_type, side, qty, price=None, params=None):
            if order_type == "limit":
                order = {
                    "id": str(len(self.orders) + 1),
                    "symbol": symbol, "type": order_type, "side": side,
                    "amount": qty, "price": price, "params": params or {},
                }
                self.orders.append(order)
                return order
            return await super().create_order(symbol, order_type, side, qty, price, params)

        async def fetch_order(self, order_id, symbol):
            # 預算算出來的掛單量是 2.5，這裡模擬只成交了 1.5（部分成交）
            return {"status": "closed", "filled": 1.5, "average": 100.0}

    exchange = PartialFillExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    await account.place_limit_entry(
        "DOGE/USDT", "LONG", 100.0, amount_usdt=50.0, sl=98.0, tp=103.0,
        reason="test", atr=1.0, leverage=5, signal_score=100,
        entry_context={
            "btc_regime_at_entry": "CONTRARY",
            "btc_direction_1h_at_entry": -1,
            "btc_score_penalty": 12,
            "btc_allocation_factor": 0.5,
            "btc_pre_penalty_score": 102,
        },
    )
    assert exchange.orders[0]["params"]["timeInForce"] == "GTX"
    await account.check_pending_limit_orders()

    open_trade = account.trades[0]
    assert open_trade["action"] == "OPEN_LONG"
    assert open_trade["qty"] == 1.5
    # 實際金額 = 1.5 * 100 / 5 = 30，不是原本打算的 50
    assert open_trade["amount"] == pytest.approx(30.0)
    assert open_trade["btc_regime_at_entry"] == "CONTRARY"
    assert open_trade["btc_direction_1h_at_entry"] == -1
    assert open_trade["btc_score_penalty"] == 12
    assert open_trade["btc_allocation_factor"] == pytest.approx(0.5)


@pytest.mark.anyio
async def test_repeated_pending_limit_retries_log_only_once(tmp_path, monkeypatch):
    """同一 symbol 連續掛單-撤單（未成交/條件變差）不設冷卻，可以無限次
    重試，但畫面上只印第一次「掛單/撤銷」，之後同樣的循環不再重複印，
    避免同一個 symbol 洗版——底層撤單/重掛的邏輯本身每次都正常執行。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )

    class RetryExchange(FakeTestnetExchange):
        async def create_order(self, symbol, order_type, side, qty, price=None, params=None):
            if order_type == "limit":
                order = {
                    "id": str(len(self.orders) + 1),
                    "symbol": symbol, "type": order_type, "side": side,
                    "amount": qty, "price": price, "params": params or {},
                }
                self.orders.append(order)
                return order
            return await super().create_order(symbol, order_type, side, qty, price, params)

        async def cancel_order(self, order_id, symbol):
            return {}

        async def fetch_order(self, order_id, symbol):
            return {"status": "canceled", "filled": 0.0}

    exchange = RetryExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    for _ in range(5):
        placed = await account.place_limit_entry(
            "DOGE/USDT", "LONG", 100.0, amount_usdt=50.0, sl=98.0, tp=103.0,
            reason="test", atr=1.0, leverage=5, signal_score=100,
        )
        assert placed is True
        await account.cancel_pending_limit("DOGE/USDT", "掛單 30 秒未成交，放棄本次進場")

    assert len(exchange.orders) == 5  # 底層真的掛了 5 次單，不受日誌節流影響
    place_logs = [entry for entry in account.logs if "限價掛單" in entry["text"]]
    cancel_logs = [entry for entry in account.logs if "限價單撤銷" in entry["text"]]
    assert len(place_logs) == 1
    assert len(cancel_logs) == 1


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
async def test_open_position_rejects_zero_amount(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    success = await account.open_position(
        "DOGE/USDT", "LONG", 100.0, 0.0, 98.0, 103.0, "test",
        atr=1.0, leverage=5, signal_score=100,
    )

    assert success is False
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


@pytest.mark.anyio
async def test_percentage_trailing_stop_updates_sl_and_removes_tp(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    account.position_meta["DOGE/USDT"] = {
        "sl": 98.0,
        "tp": 105.0,
        "atr": 1.0,
        "highest_pnl_pct": 0.0,
    }
    exchange.positions = [{
        "symbol": "DOGEUSDT",
        "positionAmt": "10",
        "entryPrice": "100.0",
        "markPrice": "101.0",
        "leverage": "5",
        "unRealizedProfit": "10",
    }]
    account.last_sync_at = 0
    previous_order_count = len(exchange.orders)

    await account.update_positions({"DOGE/USDT": 101.0})

    meta = account.position_meta["DOGE/USDT"]
    assert meta["highest_pnl_pct"] == pytest.approx(0.01)
    assert meta["is_breakeven_moved"] is True
    assert meta["tp"] == 0.0
    assert account.positions["DOGE/USDT"]["tp"] == 0.0
    assert any(order["type"] == "STOP" for order in exchange.orders[previous_order_count:])


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
async def test_disable_take_profit_prevents_tp_order(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "DISABLE_TAKE_PROFIT", True)
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)

    await account.initialize()
    success = await account.open_position(
        "DOGE/USDT",
        "LONG",
        100.0,
        50.0,
        98.0,
        103.0,
        "Fast_Keltner_SuperTrend",
        atr=1.0,
        leverage=5,
        signal_score=100,
    )

    assert success is True
    # Should only place market entry and STOP (SL), no TAKE_PROFIT_MARKET
    assert [order["type"] for order in exchange.orders] == [
        "market",
        "STOP",
    ]
    assert "DOGE/USDT" in account.positions
    assert account.positions["DOGE/USDT"]["tp"] == 0.0


@pytest.mark.anyio
async def test_partial_close_position(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    # Open LONG position
    await account.open_position(
        "DOGE/USDT", "LONG", 100.0, amount_usdt=50.0, sl=95.0, tp=105.0, reason="test", leverage=5
    )

    pos = account.positions["DOGE/USDT"]
    qty_before = pos["qty"]

    # Partial close 50%
    success = await account.partial_close_position("DOGE/USDT", current_price=101.0, close_reason="ROE達5%減倉一半", fraction=0.5)
    assert success is True

    # Check that position still exists but qty is halved
    assert "DOGE/USDT" in account.positions
    pos_after = account.positions["DOGE/USDT"]
    assert pos_after["qty"] == qty_before * 0.5
    assert account.position_meta["DOGE/USDT"]["is_half_closed"] is True

    # Check that last trade is PARTIAL_CLOSE_LONG
    assert account.trades[0]["action"] == "PARTIAL_CLOSE_LONG"
    assert account.trades[0]["status"] == "PARTIAL_CLOSED"


