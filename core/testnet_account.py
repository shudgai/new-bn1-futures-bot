import json
import os
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.config import (
    BINANCE_API_KEY,
    BINANCE_SECRET,
    TAKER_FEE_RATE,
    get_leverage,
    get_signal_leverage,
)


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "testnet_account.json")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def get_taipei_now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now(TAIPEI_TZ).strftime(fmt)


def get_taipei_time_short() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%H:%M:%S")


class BinanceTestnetAccount:
    """Binance USDM legacy testnet execution account.

    Public market data remains on the production public endpoint. Only account,
    position and order calls use the authenticated testnet exchange passed in by
    the engine.
    """

    def __init__(self, exchange):
        self.exchange = exchange
        self.balance = 0.0
        self.available_balance = 0.0
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.positions: Dict[str, dict] = {}
        self.trades: List[dict] = []
        self.logs: List[dict] = []
        self.position_meta: Dict[str, dict] = {}
        self.closing_lock: set = set()
        self.on_trade_closed: Optional[Callable[[], None]] = None
        self.last_sync_at = 0.0
        self._markets_loaded = False
        self._load_state()

    @staticmethod
    def credentials_configured() -> bool:
        return bool(BINANCE_API_KEY and BINANCE_SECRET)

    @staticmethod
    def _raw_symbol(symbol: str) -> str:
        return symbol.upper().replace("/", "").replace(":USDT", "")

    @staticmethod
    def _clean_symbol(symbol: str) -> str:
        raw = str(symbol).upper().replace("/", "").replace(":USDT", "")
        return f"{raw[:-4]}/USDT" if raw.endswith("USDT") else raw

    def _load_state(self) -> None:
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.realized_pnl = float(data.get("realized_pnl", 0.0))
            self.trades = data.get("trades", [])
            self.logs = data.get("logs", [])
            self.position_meta = data.get("position_meta", {})
        except Exception:
            pass

    def save_state(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        payload = {
            "environment": "binance_usdm_legacy_testnet",
            "realized_pnl": self.realized_pnl,
            "trades": self.trades,
            "logs": self.logs[-200:],
            "position_meta": self.position_meta,
        }
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def log(self, message: str, level: str = "INFO") -> None:
        self.logs.append({
            "time": get_taipei_time_short(),
            "timestamp": time.time(),
            "text": message,
            "level": level,
        })
        self.save_state()

    async def initialize(self) -> None:
        if not self.credentials_configured():
            raise RuntimeError("8006 Testnet API Key 尚未設定")
        await self.exchange.load_markets()
        self._markets_loaded = True
        await self.refresh(force=True)

    async def refresh(self, force: bool = False) -> float:
        now = time.time()
        if not force and now - self.last_sync_at < 5.0:
            return self.unrealized_pnl

        previous = dict(self.positions)
        balance_rows = await self.exchange.fapiPrivateV2GetBalance()
        usdt = next((row for row in balance_rows if row.get("asset") == "USDT"), {})
        self.balance = float(usdt.get("balance") or 0.0)
        self.available_balance = float(usdt.get("availableBalance") or self.balance)

        raw_positions = await self.exchange.fapiPrivateV2GetPositionRisk()
        active = {}
        for row in raw_positions:
            signed_qty = float(row.get("positionAmt") or 0.0)
            if abs(signed_qty) <= 0:
                continue
            symbol = self._clean_symbol(row.get("symbol", ""))
            side = "LONG" if signed_qty > 0 else "SHORT"
            qty = abs(signed_qty)
            entry_price = float(row.get("entryPrice") or 0.0)
            mark_price = float(row.get("markPrice") or entry_price)
            leverage = int(float(row.get("leverage") or get_leverage(symbol)))
            meta = self.position_meta.get(symbol, {})
            active[symbol] = {
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "qty": qty,
                "margin": abs(entry_price * qty) / max(leverage, 1),
                "leverage": leverage,
                "sl": float(meta.get("sl") or 0.0),
                "tp": float(meta.get("tp") or 0.0),
                "atr": float(meta.get("atr") or entry_price * 0.015),
                "is_breakeven_moved": bool(meta.get("is_breakeven_moved", False)),
                "highest_price": float(meta.get("highest_price") or entry_price),
                "lowest_price": float(meta.get("lowest_price") or entry_price),
                "peak_profit_pct": float(meta.get("peak_profit_pct") or 0.0),
                "open_timestamp": float(meta.get("open_timestamp") or now),
                "open_time": meta.get("open_time") or get_taipei_now_str(),
                "reason": meta.get("reason") or "Binance Testnet existing position",
                "signal_score": meta.get("signal_score"),
                "mark_price": mark_price,
                "unrealized_pnl": float(row.get("unRealizedProfit") or 0.0),
            }

        self.positions = active
        self.unrealized_pnl = sum(
            float(position.get("unrealized_pnl") or 0.0)
            for position in active.values()
        )
        self.last_sync_at = now

        for symbol, old_position in previous.items():
            if symbol in active or symbol in self.closing_lock:
                continue
            await self._record_external_close(symbol, old_position)

        active_symbols = set(active)
        for symbol in list(self.position_meta):
            if symbol not in active_symbols and symbol not in self.closing_lock:
                self.position_meta.pop(symbol, None)
        self.save_state()
        return self.unrealized_pnl

    async def _record_external_close(self, symbol: str, position: dict) -> None:
        await self._cancel_all_orders(symbol)
        pnl = float(position.get("unrealized_pnl") or 0.0)
        self.realized_pnl += pnl
        self.trades.insert(0, {
            "id": int(time.time() * 1000),
            "time": get_taipei_now_str("%m/%d %H:%M:%S"),
            "symbol": symbol,
            "action": f"CLOSE_{position['side']}",
            "side": position["side"],
            "price": float(position.get("mark_price") or position["entry_price"]),
            "qty": position["qty"],
            "amount": position.get("margin", 0.0),
            "fee": 0.0,
            "pnl": round(pnl, 4),
            "status": "CLOSED",
            "reason": "Binance Testnet 保護單成交",
        })
        self.log(
            f"🏁 Binance Testnet 已平倉 [{position['side']}] {symbol} | "
            f"估算損益: {pnl:+.2f} USDT",
            "SUCCESS" if pnl >= 0 else "DANGER",
        )
        if self.on_trade_closed:
            try:
                self.on_trade_closed()
            except Exception:
                pass

    async def update_positions(self, ticker_prices: Dict[str, float]) -> float:
        return await self.refresh()

    async def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            await self.exchange.load_markets()
            self._markets_loaded = True

    async def _cancel_all_orders(self, symbol: str) -> None:
        try:
            await self.exchange.cancel_all_orders(symbol)
        except Exception:
            pass
        try:
            await self.exchange.request(
                "algoOpenOrders",
                "fapiPrivate",
                "DELETE",
                {"symbol": self._raw_symbol(symbol)},
            )
        except Exception:
            pass

    async def _create_protection_order(
        self, symbol: str, side: str, order_type: str, qty: float, trigger_price: float
    ) -> dict:
        return await self.exchange.request(
            "algoOrder",
            "fapiPrivate",
            "POST",
            {
                "algoType": "CONDITIONAL",
                "symbol": self._raw_symbol(symbol),
                "side": side.upper(),
                "type": order_type,
                "quantity": self.exchange.amount_to_precision(symbol, qty),
                "triggerPrice": self.exchange.price_to_precision(symbol, trigger_price),
                "reduceOnly": "true",
                "workingType": "MARK_PRICE",
            },
        )

    async def _emergency_flatten(self, symbol: str, side: str, qty: float) -> None:
        close_side = "sell" if side == "LONG" else "buy"
        try:
            await self.exchange.create_order(
                symbol,
                "market",
                close_side,
                qty,
                None,
                {"reduceOnly": True},
            )
        except Exception as exc:
            self.log(
                f"🚨 {symbol} 保護單建立失敗且緊急平倉也失敗："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            raise

    async def open_position(
        self,
        symbol: str,
        side: str,
        price: float,
        amount_usdt: float,
        sl: float,
        tp: float,
        reason: str,
        atr: float = 0.0,
        leverage: int = None,
        signal_score: int = None,
    ) -> bool:
        if symbol in self.positions or symbol in self.closing_lock:
            return False
        await self._ensure_markets()
        leverage = leverage or (
            get_signal_leverage(symbol, signal_score)
            if signal_score is not None
            else get_leverage(symbol)
        )
        order_side = "buy" if side == "LONG" else "sell"
        close_side = "sell" if side == "LONG" else "buy"
        qty = float(self.exchange.amount_to_precision(
            symbol,
            (amount_usdt * leverage) / max(price, 1e-12),
        ))
        if qty <= 0:
            self.log(f"🛑 {symbol} 下單數量低於交易所最小精度", "WARNING")
            return False

        try:
            await self.exchange.set_leverage(leverage, symbol)
            entry_order = await self.exchange.create_order(
                symbol,
                "market",
                order_side,
                qty,
                None,
                {"newOrderRespType": "RESULT"},
            )
            execution_price = float(entry_order.get("average") or price)
            sl_distance = abs(price - sl)
            tp_distance = abs(tp - price)
            adjusted_sl = (
                execution_price - sl_distance if side == "LONG"
                else execution_price + sl_distance
            )
            adjusted_tp = (
                execution_price + tp_distance if side == "LONG"
                else execution_price - tp_distance
            )
            sl_price = float(self.exchange.price_to_precision(symbol, adjusted_sl))
            tp_price = float(self.exchange.price_to_precision(symbol, adjusted_tp))
            try:
                await self._create_protection_order(
                    symbol, close_side, "STOP_MARKET", qty, sl_price
                )
                await self._create_protection_order(
                    symbol, close_side, "TAKE_PROFIT_MARKET", qty, tp_price
                )
            except Exception:
                await self._cancel_all_orders(symbol)
                await self._emergency_flatten(symbol, side, qty)
                raise

            fee = qty * execution_price * TAKER_FEE_RATE
            meta = {
                "sl": sl_price,
                "tp": tp_price,
                "atr": atr if atr > 0 else execution_price * 0.015,
                "open_timestamp": time.time(),
                "open_time": get_taipei_now_str(),
                "reason": reason,
                "signal_score": signal_score,
                "highest_price": execution_price,
                "lowest_price": execution_price,
                "peak_profit_pct": 0.0,
                "is_breakeven_moved": False,
            }
            self.position_meta[symbol] = meta
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"OPEN_{side}",
                "side": side,
                "price": round(execution_price, 8),
                "qty": qty,
                "amount": amount_usdt,
                "fee": round(fee, 4),
                "pnl": 0.0,
                "status": "OPEN",
                "leverage": leverage,
                "signal_score": signal_score,
                "reason": reason,
                "sl": sl_price,
                "tp": tp_price,
                "exchange_order_id": entry_order.get("id"),
            })
            await self.refresh(force=True)
            self.log(
                f"🚀 Binance Testnet 開倉成功 [{side}] {symbol} @ "
                f"{execution_price:.6f} ({leverage}x，SL={sl_price}, TP={tp_price})",
                "SUCCESS",
            )
            return True
        except Exception as exc:
            self.log(
                f"🛑 Binance Testnet 開倉失敗 {symbol}："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            await self.refresh(force=True)
            return False

    async def close_position(
        self, symbol: str, current_price: float, close_reason: str
    ) -> bool:
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        self.closing_lock.add(symbol)
        position = dict(self.positions[symbol])
        try:
            await self._cancel_all_orders(symbol)
            close_side = "sell" if position["side"] == "LONG" else "buy"
            order = await self.exchange.create_order(
                symbol,
                "market",
                close_side,
                position["qty"],
                None,
                {"reduceOnly": True, "newOrderRespType": "RESULT"},
            )
            execution_price = float(order.get("average") or current_price)
            raw_pnl = (
                (execution_price - position["entry_price"]) * position["qty"]
                if position["side"] == "LONG"
                else (position["entry_price"] - execution_price) * position["qty"]
            )
            fee = (
                position["entry_price"] + execution_price
            ) * position["qty"] * TAKER_FEE_RATE
            net_pnl = raw_pnl - fee
            self.realized_pnl += net_pnl
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"CLOSE_{position['side']}",
                "side": position["side"],
                "price": execution_price,
                "qty": position["qty"],
                "amount": position.get("margin", 0.0),
                "fee": round(fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": close_reason,
                "exchange_order_id": order.get("id"),
            })
            self.position_meta.pop(symbol, None)
            await self.refresh(force=True)
            self.log(
                f"🏁 Binance Testnet 平倉 [{position['side']}] {symbol} @ "
                f"{execution_price:.6f} | 淨損益: {net_pnl:+.2f} USDT ({close_reason})",
                "SUCCESS" if net_pnl >= 0 else "DANGER",
            )
            if self.on_trade_closed:
                try:
                    self.on_trade_closed()
                except Exception:
                    pass
            return True
        except Exception as exc:
            self.log(
                f"🚨 Binance Testnet 平倉失敗 {symbol}："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            return False
        finally:
            self.closing_lock.discard(symbol)
