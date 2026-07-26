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
    TRAILING_TRIGGER_PCT,
    NET_PROFIT_GUARANTEE_BUFFER,
    STOP_LOSS_MULTIPLIER,
    TAKE_PROFIT_MULTIPLIER,
    PARTIAL_CLOSE_THRESHOLDS,
    get_leverage,
    get_signal_leverage,
    get_trailing_pullback_pct,
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
        self._last_trailing_log: Dict[str, float] = {}
        self._trailing_lock: set = set()
        self._orphan_protection_attempted: set = set()
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

    def get_available_balance(self) -> float:
        return max(0.0, self.available_balance)

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
                "peak_profit_updated_at": float(meta.get("peak_profit_updated_at") or 0.0),
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
        if symbol in self.closing_lock:
            return
        self.closing_lock.add(symbol)
        try:
            await self._cancel_all_orders(symbol)
            self.positions.pop(symbol, None)
            close_price = float(position.get("mark_price") or position["entry_price"])
            gross_pnl = float(position.get("unrealized_pnl") or 0.0)
            open_fee = position["entry_price"] * position["qty"] * TAKER_FEE_RATE
            close_fee = close_price * position["qty"] * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = gross_pnl - total_fee
            self.realized_pnl += net_pnl
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"CLOSE_{position['side']}",
                "side": position["side"],
                "price": close_price,
                "qty": position["qty"],
                "amount": position.get("margin", 0.0),
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": "Binance Testnet 保護單成交",
            })
            self.log(
                f"🏁 Binance Testnet 已平倉 [{position['side']}] {symbol} | "
                f"損益: {net_pnl:+.2f} USDT (手續費: {total_fee:.4f})",
                "SUCCESS" if net_pnl >= 0 else "DANGER",
            )
            if self.on_trade_closed:
                try:
                    self.on_trade_closed()
                except Exception:
                    pass
        finally:
            self.closing_lock.discard(symbol)

    async def update_positions(self, ticker_prices: Dict[str, float]) -> float:
        await self.refresh()

        for symbol, pos in list(self.positions.items()):
            curr_p = ticker_prices.get(symbol) or ticker_prices.get(f"{symbol}:USDT") or ticker_prices.get(symbol.replace('/USDT', ''))
            if curr_p is None:
                continue

            meta = self.position_meta.get(symbol, {})
            side = pos["side"]
            entry_p = pos["entry_price"]

            if pos.get("sl", 0.0) <= 0 and symbol not in self._orphan_protection_attempted:
                self._orphan_protection_attempted.add(symbol)
                await self._create_orphan_protection(symbol, pos, meta)

            if "peak_profit_pct" not in meta:
                meta["peak_profit_pct"] = 0.0
            if "peak_profit_updated_at" not in meta:
                meta["peak_profit_updated_at"] = pos.get("open_timestamp", time.time())

            if side == "LONG":
                curr_profit_pct = (curr_p - entry_p) / entry_p
            else:
                curr_profit_pct = (entry_p - curr_p) / entry_p

            if curr_profit_pct > meta.get("peak_profit_pct", 0.0):
                meta["peak_profit_pct"] = curr_profit_pct
                meta["peak_profit_updated_at"] = time.time()

            peak = meta.get("peak_profit_pct", 0.0)
            old_sl = pos.get("sl", 0.0)

            if peak >= TRAILING_TRIGGER_PCT and old_sl > 0:
                pullback = get_trailing_pullback_pct(peak, meta.get("peak_profit_updated_at", time.time()))
                if side == "LONG":
                    trail_sl = entry_p * (1.0 + peak * pullback)
                    npg_floor = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = max(trail_sl, npg_floor)
                    if trail_sl > old_sl:
                        new_sl = float(self.exchange.price_to_precision(symbol, trail_sl))
                        await self._replace_stop_order(symbol, side, pos["qty"], old_sl, new_sl, meta, pos)
                        now_ts = time.time()
                        if now_ts - self._last_trailing_log.get(symbol, 0) >= 30:
                            self._last_trailing_log[symbol] = now_ts
                            self.log(
                                f"📈 [移動止利] {symbol} 無槓桿利潤峰值 {peak:.4%}，止利線推至 {new_sl}（回吐 {1-pullback:.0%} 平倉）",
                                "SUCCESS",
                            )
                else:
                    trail_sl = entry_p * (1.0 - peak * pullback)
                    npg_ceiling = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = min(trail_sl, npg_ceiling)
                    if trail_sl < old_sl:
                        new_sl = float(self.exchange.price_to_precision(symbol, trail_sl))
                        await self._replace_stop_order(symbol, side, pos["qty"], old_sl, new_sl, meta, pos)
                        now_ts = time.time()
                        if now_ts - self._last_trailing_log.get(symbol, 0) >= 30:
                            self._last_trailing_log[symbol] = now_ts
                            self.log(
                                f"📉 [移動止利] {symbol} 無槓桿利潤峰值 {peak:.4%}，止利線推至 {new_sl}（回吐 {1-pullback:.0%} 平倉）",
                                "SUCCESS",
                            )

            if (time.time() - pos.get("open_timestamp", time.time())) >= 86400:
                await self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            phase = meta.get("partial_close_phase", 0)
            for i, (threshold, ratio) in enumerate(PARTIAL_CLOSE_THRESHOLDS):
                if phase <= i and curr_profit_pct >= threshold:
                    await self._partial_close(symbol, pos, meta, ratio, curr_profit_pct, i + 1)
                    break

            self.position_meta[symbol] = meta

        self.save_state()
        return self.unrealized_pnl

    async def _replace_stop_order(
        self, symbol: str, side: str, qty: float, old_sl: float, new_sl: float, meta: dict, pos: dict
    ) -> None:
        # 避免同一 symbol 的移動止利在 refresh() 節流窗口內被多個呼叫者
        # (/api/prices、/api/status、主循環) 重複觸發，造成保護單反覆 cancel/recreate。
        if symbol in self._trailing_lock:
            return
        self._trailing_lock.add(symbol)
        close_side = "sell" if side == "LONG" else "buy"
        try:
            await self._cancel_all_orders(symbol)
            try:
                await self._create_protection_order(
                    symbol, close_side, "STOP_MARKET", qty, new_sl
                )
            except Exception as exc:
                # 新止損價已經被市價（MARK_PRICE）穿越，交易所拒絕掛單
                # （-2021 Order would immediately trigger）。舊保護單已被取消，
                # 若放著不管部位會完全裸奔，直接市價平倉，等同止損已觸發。
                self.log(
                    f"⚠️ {symbol} 移動止利新止損建立失敗（{type(exc).__name__}: {exc}），"
                    f"研判價格已穿越止利線，改為市價平倉",
                    "DANGER",
                )
                await self.close_position(symbol, new_sl, "移動止利保護單被拒，市價平倉")
                return
            meta["sl"] = new_sl
            self.position_meta[symbol] = meta
            tp = meta.get("tp") or 0.0
            if tp > 0:
                try:
                    await self._create_protection_order(
                        symbol, close_side, "TAKE_PROFIT_MARKET", qty, tp
                    )
                except Exception as exc:
                    self.log(
                        f"⚠️ {symbol} 移動止利後止盈單重建失敗：{type(exc).__name__}: {exc}"
                        f"（止損已生效，僅缺止盈保護）",
                        "WARNING",
                    )
            # 直接同步 pos["sl"]，不必等下一次節流的 refresh() 才能看到新止利價，
            # 否則後續 tick 會拿舊值重複判斷「有改善」而再次觸發本函式。
            pos["sl"] = new_sl
        except Exception as exc:
            self.log(
                f"⚠️ {symbol} 移動止利保護單更新失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )
        finally:
            self._trailing_lock.discard(symbol)

    async def _create_orphan_protection(self, symbol: str, pos: dict, meta: dict) -> None:
        side = pos["side"]
        entry_p = pos["entry_price"]
        atr = meta.get("atr") or entry_p * 0.015
        if side == "LONG":
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p - atr * STOP_LOSS_MULTIPLIER))
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p + atr * TAKE_PROFIT_MULTIPLIER))
        else:
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p + atr * STOP_LOSS_MULTIPLIER))
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p - atr * TAKE_PROFIT_MULTIPLIER))
        close_side = "sell" if side == "LONG" else "buy"
        try:
            await self._cancel_all_orders(symbol)
            await self._create_protection_order(symbol, close_side, "STOP_MARKET", pos["qty"], sl_price)
            await self._create_protection_order(symbol, close_side, "TAKE_PROFIT_MARKET", pos["qty"], tp_price)
            meta["sl"] = sl_price
            meta["tp"] = tp_price
            meta["atr"] = atr
            self.position_meta[symbol] = meta
            self.save_state()
            self.log(
                f"🔧 [補建保護單] {symbol} 偵測到無止損止盈持倉，已自動建立 SL={sl_price} TP={tp_price}",
                "WARNING",
            )
        except Exception as exc:
            self.log(
                f"⚠️ {symbol} 補建保護單失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )

    async def _partial_close(
        self, symbol: str, pos: dict, meta: dict, ratio: float, profit_pct: float, phase: int
    ) -> None:
        if symbol in self.closing_lock:
            return
        self.closing_lock.add(symbol)
        try:
            close_qty_raw = pos["qty"] * ratio
            close_qty = float(self.exchange.amount_to_precision(symbol, close_qty_raw))
            if close_qty <= 0:
                return
            await self._cancel_all_orders(symbol)
            close_side = "sell" if pos["side"] == "LONG" else "buy"
            order = await self.exchange.create_order(
                symbol, "market", close_side, close_qty, None,
                {"reduceOnly": True, "newOrderRespType": "RESULT"},
            )
            exec_price = float(order.get("average") or 0)
            if pos["side"] == "LONG":
                raw_pnl = (exec_price - pos["entry_price"]) * close_qty
            else:
                raw_pnl = (pos["entry_price"] - exec_price) * close_qty
            open_fee = pos["entry_price"] * close_qty * TAKER_FEE_RATE
            close_fee = exec_price * close_qty * TAKER_FEE_RATE
            fee = open_fee + close_fee
            net_pnl = raw_pnl - fee
            self.realized_pnl += net_pnl
            remaining_qty = pos["qty"] - close_qty
            remaining_margin = pos.get("margin", 0.0) * (remaining_qty / pos["qty"])
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"PARTIAL_CLOSE_{pos['side']}",
                "side": pos["side"],
                "price": exec_price,
                "qty": close_qty,
                "amount": remaining_margin,
                "fee": round(fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": f"分批止盈 (Phase {phase}, 利潤 {profit_pct:.1%})",
                "exchange_order_id": order.get("id"),
            })
            old_sl = meta.get("sl", 0.0)
            meta["partial_close_phase"] = phase
            meta["sl"] = old_sl
            meta["partial_close_qty"] = close_qty
            self.position_meta[symbol] = meta
            new_sl = float(self.exchange.price_to_precision(symbol, old_sl))
            tp = meta.get("tp") or 0.0
            await self._create_protection_order(
                symbol, close_side, "STOP_MARKET", remaining_qty, new_sl
            )
            if tp > 0:
                await self._create_protection_order(
                    symbol, close_side, "TAKE_PROFIT_MARKET", remaining_qty, tp
                )
            self.log(
                f"💰 [分批止盈] {symbol} 平倉 {ratio:.0%}（{close_qty:.2f}）@ {exec_price:.6f} | "
                f"淨損益: {net_pnl:+.2f} USDT | 剩餘 {remaining_qty:.2f} 繼續持有",
                "SUCCESS",
            )
            await self.refresh(force=True)
        except Exception as exc:
            self.log(
                f"⚠️ {symbol} 分批止盈失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )
        finally:
            self.closing_lock.discard(symbol)

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
                "peak_profit_updated_at": time.time(),
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
            open_fee = position["entry_price"] * position["qty"] * TAKER_FEE_RATE
            close_fee = execution_price * position["qty"] * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
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
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": close_reason,
                "exchange_order_id": order.get("id"),
            })
            self.position_meta.pop(symbol, None)
            self.positions.pop(symbol, None)
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
