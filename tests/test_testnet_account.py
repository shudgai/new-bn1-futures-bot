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
async def test_testnet_account_places_entry_stop_and_take_profit(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "DISABLE_TAKE_PROFIT", False)
    monkeypatch.setattr(testnet_module, "ENABLE_EXCHANGE_INITIAL_STOP_LOSS", True)
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
        "STOP_MARKET",
        "TAKE_PROFIT_MARKET",
    ]
    assert exchange.orders[1]["params"]["reduceOnly"] == "true"
    assert exchange.orders[2]["params"]["reduceOnly"] == "true"


@pytest.mark.anyio
async def test_disabled_exchange_initial_stop_waits_until_local_max_loss(
    tmp_path, monkeypatch
):
    """停用交易所初始 STOP 後仍保留虛擬 SL；小幅穿越不平倉，
    只有達本地最大虧損門檻才退出。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "DISABLE_TAKE_PROFIT", False)
    monkeypatch.setattr(testnet_module, "ENABLE_EXCHANGE_INITIAL_STOP_LOSS", False)
    monkeypatch.setattr(testnet_module, "ENABLE_TRAILING_STOP", False)
    monkeypatch.setattr(testnet_module, "MAX_ACCEPTABLE_LOSS_PCT", -0.02)
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)

    await account.initialize()
    success = await account.open_position(
        "DOGE/USDT", "LONG", 100.0, 50.0, 99.5, 103.0,
        "MA7_Reversal_LONG", atr=1.0, leverage=5, signal_score=88,
    )

    assert success is True
    assert [order["type"] for order in exchange.orders] == [
        "market", "TAKE_PROFIT_MARKET",
    ]
    assert account.position_meta["DOGE/USDT"]["sl"] == pytest.approx(99.5)

    close_reasons = []

    async def record_close(_symbol, _price, reason):
        close_reasons.append(reason)
        return True

    monkeypatch.setattr(account, "close_position", record_close)

    exchange.positions[0]["markPrice"] = "99.0"
    exchange.positions[0]["unRealizedProfit"] = "-10"
    account.last_sync_at = 0
    await account.update_positions({"DOGE/USDT": 99.0})
    assert close_reasons == []

    exchange.positions[0]["markPrice"] = "97.5"
    exchange.positions[0]["unRealizedProfit"] = "-25"
    account.last_sync_at = 0
    await account.update_positions({"DOGE/USDT": 97.5})
    assert close_reasons == ["本地最大虧損門檻觸發"]


@pytest.mark.anyio
async def test_initialize_restores_exchange_stop_for_position_opened_in_local_mode(
    tmp_path, monkeypatch
):
    """切回交易所硬停損後重啟，舊持倉不可因只留本地 SL 而變成裸倉。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "ENABLE_EXCHANGE_INITIAL_STOP_LOSS", True)
    monkeypatch.setattr(testnet_module, "DISABLE_TAKE_PROFIT", False)
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    exchange.positions = [{
        "symbol": "DOGEUSDT",
        "positionAmt": "10",
        "entryPrice": "100",
        "markPrice": "100",
        "leverage": "5",
        "unRealizedProfit": "0",
    }]
    account = BinanceTestnetAccount(exchange)
    account.position_meta["DOGE/USDT"] = {
        "sl": 98.0,
        "tp": 104.0,
        "atr": 1.0,
    }

    await account.initialize()

    restored = [
        order for order in exchange.orders
        if order["type"] in ("STOP_MARKET", "TAKE_PROFIT_MARKET")
    ]
    assert [order["type"] for order in restored] == [
        "STOP_MARKET", "TAKE_PROFIT_MARKET",
    ]
    assert restored[0]["params"]["triggerPrice"] == "98.0"
    assert any("啟動保護遷移" in entry["text"] for entry in account.logs)



@pytest.mark.anyio
async def test_non_post_only_entry_executes_immediately_as_market(tmp_path, monkeypatch):
    """MA7 指定 post_only=False 時必須立即成交，不可留下 GTC 掛單。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "DISABLE_TAKE_PROFIT", False)
    monkeypatch.setattr(
        BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    success = await account.place_limit_entry(
        "DOGE/USDT", "LONG", 100.0, amount_usdt=50.0, sl=98.0, tp=103.0,
        reason="MA7_Reversal_LONG", atr=1.0, leverage=5, signal_score=89,
        post_only=False, entry_context={"entry_mode": "MA7_REVERSAL"},
    )

    assert success is True
    assert exchange.orders[0]["type"] == "market"
    assert "DOGE/USDT" in account.positions
    assert account.pending_limit_orders == {}


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
async def test_place_limit_entry_recovers_after_timeout_when_order_actually_placed(tmp_path, monkeypatch):
    """create_order 逾時（執行狀態未知，例如幣安 -1007）時不能直接當失敗
    結束——單子可能其實已經在交易所端掛成功了。查詢 fetch_open_orders()
    如果真的撈到吻合的單，要接手追蹤（回傳True），不能讓主迴圈下一輪
    用同一份訊號重複掛單，在交易所端疊出孤兒限價單。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(testnet_module.asyncio, "sleep", lambda *_a, **_k: asyncio_sleep_stub())

    class TimeoutButActuallyPlacedExchange(FakeTestnetExchange):
        async def create_order(self, symbol, order_type, side, qty, price=None, params=None):
            if order_type == "limit":
                raise ccxt.RequestTimeout("binanceusdm timeout, unknown execution status")
            return await super().create_order(symbol, order_type, side, qty, price, params)

        async def fetch_open_orders(self, symbol):
            return [{"id": "999", "symbol": symbol, "type": "limit", "side": "buy", "price": 100.0}]

    exchange = TimeoutButActuallyPlacedExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    placed = await account.place_limit_entry(
        "DOGE/USDT", "LONG", 100.0, amount_usdt=50.0, sl=98.0, tp=103.0,
        reason="test", atr=1.0, leverage=5, signal_score=100,
    )

    assert placed is True
    assert "DOGE/USDT" in account.pending_limit_orders
    assert account.pending_limit_orders["DOGE/USDT"]["order_id"] == "999"
    assert any("查詢確認已成功掛上" in entry["text"] for entry in account.logs)


@pytest.mark.anyio
async def test_place_limit_entry_fails_after_timeout_when_no_order_found(tmp_path, monkeypatch):
    """逾時後查詢交易所也真的查無此單，才能算失敗結束，安全地讓下一輪
    重新掛單。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(testnet_module.asyncio, "sleep", lambda *_a, **_k: asyncio_sleep_stub())

    class TimeoutAndNeverPlacedExchange(FakeTestnetExchange):
        async def create_order(self, symbol, order_type, side, qty, price=None, params=None):
            if order_type == "limit":
                raise ccxt.RequestTimeout("binanceusdm timeout, unknown execution status")
            return await super().create_order(symbol, order_type, side, qty, price, params)

        async def fetch_open_orders(self, symbol):
            return []

    exchange = TimeoutAndNeverPlacedExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    placed = await account.place_limit_entry(
        "DOGE/USDT", "LONG", 100.0, amount_usdt=50.0, sl=98.0, tp=103.0,
        reason="test", atr=1.0, leverage=5, signal_score=100,
    )

    assert placed is False
    assert "DOGE/USDT" not in account.pending_limit_orders
    assert any("查無成交紀錄" in entry["text"] for entry in account.logs)


@pytest.mark.anyio
async def test_place_limit_entry_timeout_before_price_str_assigned_does_not_crash(tmp_path, monkeypatch):
    """set_leverage（_prepare_leverage 內部）逾時時，price_str 這時還沒
    賦值——實測曾在這裡觸發 UnboundLocalError（'cannot access local
    variable price_str where it is not associated with a value'），把
    整個主迴圈拖垮。price_str 現在移到 try 區塊外先算好，這裡確保逾時
    發生在 create_order 之前也不會炸掉。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(testnet_module.asyncio, "sleep", lambda *_a, **_k: asyncio_sleep_stub())

    class LeverageTimeoutExchange(FakeTestnetExchange):
        async def set_leverage(self, leverage, symbol):
            raise ccxt.RequestTimeout("binanceusdm timeout, unknown execution status")

        async def fetch_open_orders(self, symbol):
            return []

    exchange = LeverageTimeoutExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()

    placed = await account.place_limit_entry(
        "DOGE/USDT", "LONG", 100.0, amount_usdt=50.0, sl=98.0, tp=103.0,
        reason="test", atr=1.0, leverage=5, signal_score=100,
    )

    assert placed is False
    assert any("查無成交紀錄" in entry["text"] for entry in account.logs)


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
async def test_percentage_trailing_stop_updates_sl_and_removes_tp(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "ENABLE_TRAILING_STOP", True)
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "USE_NATIVE_TRAILING_STOP", False)
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
    assert any(order["type"] == "STOP_MARKET" for order in exchange.orders[previous_order_count:])


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
async def test_disable_take_profit_prevents_tp_order(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "DISABLE_TAKE_PROFIT", True)
    monkeypatch.setattr(testnet_module, "ENABLE_EXCHANGE_INITIAL_STOP_LOSS", True)
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
    # Should only place market entry and STOP_MARKET (SL), no TAKE_PROFIT_MARKET
    assert [order["type"] for order in exchange.orders] == [
        "market",
        "STOP_MARKET",
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
async def test_native_trailing_failure_restores_fixed_stop(tmp_path, monkeypatch):
    """原生追蹤掛單失敗後，必須補回固定止損且短時間內不反覆撤掛。"""
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(testnet_module, "ENABLE_TRAILING_STOP", True)
    monkeypatch.setattr(testnet_module, "USE_NATIVE_TRAILING_STOP", True)
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )

    class TrailingRejectedExchange(FakeTestnetExchange):
        def __init__(self):
            super().__init__()
            self.request_calls = []
            self.trailing_attempts = 0

        async def request(self, path, api, method, params):
            self.request_calls.append((path, api, method, dict(params)))
            if method == "POST" and params.get("type") == "TRAILING_STOP_MARKET":
                self.trailing_attempts += 1
                raise ccxt.ExchangeError("binanceusdm error -4120")
            return await super().request(path, api, method, params)

    exchange = TrailingRejectedExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()
    account.position_meta["DOGE/USDT"] = {
        "sl": 98.0,
        "tp": 0.0,
        "atr": 1.0,
        "highest_pnl_pct": 0.0,
    }
    exchange.positions = [{
        "symbol": "DOGEUSDT",
        "positionAmt": "10",
        "entryPrice": "100.0",
        "markPrice": "106.0",
        "leverage": "5",
        "unRealizedProfit": "60",
    }]
    account.last_sync_at = 0

    await account.update_positions({"DOGE/USDT": 106.0})

    meta = account.position_meta["DOGE/USDT"]
    restored_stops = [
        params for path, _api, method, params in exchange.request_calls
        if path == "algoOrder" and method == "POST" and params.get("type") == "STOP_MARKET"
    ]
    assert exchange.trailing_attempts == 1
    assert restored_stops
    assert meta["sl"] > 100.0
    assert meta["native_trailing_retry_after"] > 0
    assert any("固定止損已恢復" in entry["text"] for entry in account.logs)

    await account.update_positions({"DOGE/USDT": 106.0})
    assert exchange.trailing_attempts == 1
