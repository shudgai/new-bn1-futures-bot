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
    NET_PROFIT_GUARANTEE_BUFFER,
    PARTIAL_CLOSE_THRESHOLDS,
    FLASH_MOVE_WINDOW_SEC,
    FLASH_MOVE_THRESHOLD_PCT,
    ENTRY_GRACE_SECONDS,
    ENTRY_GRACE_EXTRA_ATR,
    MAX_DAILY_LOSS_PCT,
    CHANDELIER_ATR_MULT,
    get_leverage,
    get_signal_leverage,
)
from core.strategy import compute_sl_tp_distance
from core.notifier import notify_email


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
        self._price_history: Dict[str, List[tuple]] = {}
        self._last_flash_log: Dict[str, float] = {}
        self.daily_date: Optional[str] = None
        self.daily_start_balance: float = 0.0
        self.daily_start_realized_pnl: float = 0.0
        self.daily_halt_logged: bool = False
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
            self.daily_date = data.get("daily_date")
            self.daily_start_balance = float(data.get("daily_start_balance", 0.0))
            self.daily_start_realized_pnl = float(data.get("daily_start_realized_pnl", 0.0))
            self.daily_halt_logged = bool(data.get("daily_halt_logged", False))
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
            "daily_date": self.daily_date,
            "daily_start_balance": self.daily_start_balance,
            "daily_start_realized_pnl": self.daily_start_realized_pnl,
            "daily_halt_logged": self.daily_halt_logged,
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
        if level == "DANGER":
            notify_email(f"[Binance Bot] {message}")

    def _check_daily_reset(self) -> None:
        """台北時區跨日就重置今日虧損熔斷的計算基準。"""
        today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
        if self.daily_date != today:
            self.daily_date = today
            self.daily_start_balance = self.balance
            self.daily_start_realized_pnl = self.realized_pnl
            self.daily_halt_logged = False

    def daily_loss_limit_hit(self) -> tuple:
        """回傳 (是否觸發熔斷, 今日虧損百分比)。觸發時只暫停開新倉，
        既有持倉的止損/止利不受影響，隔天（台北時區）自動重置。"""
        if self.daily_start_balance <= 0:
            return False, 0.0
        daily_pnl = self.realized_pnl - self.daily_start_realized_pnl
        loss_pct = max(0.0, -daily_pnl / self.daily_start_balance * 100.0)
        hit = loss_pct >= MAX_DAILY_LOSS_PCT
        if hit and not self.daily_halt_logged:
            self.daily_halt_logged = True
            self.log(
                f"🛑 [每日熔斷] 今日已實現虧損 {loss_pct:.1f}%，達到門檻 "
                f"{MAX_DAILY_LOSS_PCT:.0f}%，暫停開新倉（既有持倉仍正常管理），"
                f"明日（台北時區）自動重置",
                "DANGER",
            )
        return hit, loss_pct

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
        self._check_daily_reset()

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
            # position["mark_price"]/["unrealized_pnl"] 是上一次 refresh()（最多5秒前）
            # 快取的近似值，不是真正觸發保護單當下的成交價，會跟實際損益有落差。
            # 改成向交易所查詢最近的實際成交紀錄，取真正的平倉均價來算損益。
            close_price = float(position.get("mark_price") or position["entry_price"])
            try:
                close_side = "sell" if position["side"] == "LONG" else "buy"
                recent_trades = await self.exchange.fetch_my_trades(symbol, limit=5)
                cutoff_ms = (time.time() - 300) * 1000
                closing_fills = [
                    t for t in recent_trades
                    if t.get("side") == close_side and (t.get("timestamp") or 0) >= cutoff_ms
                ]
                fill_qty = sum(float(t["amount"]) for t in closing_fills)
                if fill_qty > 0:
                    close_price = sum(float(t["price"]) * float(t["amount"]) for t in closing_fills) / fill_qty
            except Exception:
                pass  # 查詢失敗就退回用快取的 mark_price 估算
            # 損益改用 Binance 官方的已實現損益帳本（REALIZED_PNL income）為準，
            # 不再自己拿成交明細反推——反推容易把移動止利換單過程中的零碎成交
            # 也算進去，導致跟實際損益對不上。close_price 只用來顯示、算手續費。
            raw_pnl = None
            try:
                income_rows = await self.exchange.fapiPrivateGetIncome({
                    "symbol": self._raw_symbol(symbol),
                    "incomeType": "REALIZED_PNL",
                    "limit": 20,
                })
                cutoff_ms = (time.time() - 300) * 1000
                recent_pnl_rows = [
                    r for r in income_rows if float(r.get("time", 0)) >= cutoff_ms
                ]
                if recent_pnl_rows:
                    raw_pnl = sum(float(r.get("income", 0)) for r in recent_pnl_rows)
            except Exception:
                pass
            if raw_pnl is None:
                if position["side"] == "LONG":
                    raw_pnl = (close_price - position["entry_price"]) * position["qty"]
                else:
                    raw_pnl = (position["entry_price"] - close_price) * position["qty"]
            open_fee = position["entry_price"] * position["qty"] * TAKER_FEE_RATE
            close_fee = close_price * position["qty"] * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
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
            if "highest_price" not in meta:
                meta["highest_price"] = entry_p
            if "lowest_price" not in meta:
                meta["lowest_price"] = entry_p

            if side == "LONG":
                curr_profit_pct = (curr_p - entry_p) / entry_p
                if curr_p > meta["highest_price"]:
                    meta["highest_price"] = curr_p
            else:
                curr_profit_pct = (entry_p - curr_p) / entry_p
                if curr_p < meta["lowest_price"]:
                    meta["lowest_price"] = curr_p

            # peak_profit_pct 只留給前端顯示「歷史最高無槓桿利潤」用，出場判斷
            # 已經改用下面的 ATR 移動停利（見 highest_price/lowest_price）。
            if curr_profit_pct > meta.get("peak_profit_pct", 0.0):
                meta["peak_profit_pct"] = curr_profit_pct
                meta["peak_profit_updated_at"] = time.time()

            old_sl = pos.get("sl", 0.0)
            now_ts = time.time()
            is_flash = self._is_flash_move(symbol, curr_p, side, now_ts)
            if is_flash and now_ts - self._last_flash_log.get(symbol, 0) >= 30:
                self._last_flash_log[symbol] = now_ts
                self.log(
                    f"🌪️ [急殺辨識] {symbol} 短時間內劇烈逆勢波動，暫停收緊移動止利"
                    f"（原止損 {old_sl} 維持不變）",
                    "WARNING",
                )

            # 進場緩衝期結束：把剛進場時放寬的止損收緊回原本策略設定的正常距離。
            # 若急殺正在發生就先不收緊（避免在最劇烈的當下把止損調緊），
            # 若移動止利已經把止損推得比目標還緊，直接清掉標記、不需要再處理。
            if "target_sl" in meta and now_ts >= meta.get("grace_until", 0) and not is_flash:
                target_sl = meta["target_sl"]
                already_tighter = (
                    (side == "LONG" and old_sl >= target_sl)
                    or (side == "SHORT" and 0 < old_sl <= target_sl)
                )
                if already_tighter:
                    meta.pop("target_sl", None)
                else:
                    new_sl = float(self.exchange.price_to_precision(symbol, target_sl))
                    await self._replace_stop_order(symbol, side, pos["qty"], old_sl, new_sl, meta, pos)
                    meta.pop("target_sl", None)
                    self.log(f"⏱️ [緩衝期結束] {symbol} 止損收緊回正常距離 {new_sl}", "INFO")
                    old_sl = pos.get("sl", old_sl)

            # 移動止利改用 ATR 移動停利（chandelier exit）：不再等固定百分比
            # 才啟動。實測 328 筆歷史交易發現，中位數的「進場後最大有利幅度」
            # 只有 0.23%，47.5% 連 0.25% 都碰不到、只有 16.7% 能碰到 0.5%——
            # 固定百分比門檻對大部分幣種根本啟動不了，導致本來有一點小獲利的
            # 單子，因為到不了門檻鎖不到利，最後反轉坐雲霄飛車坐成停損（而且
            # 停損金額不小）。改成「從進場後出現過的最高價（多單）/最低價
            # （空單）回吐 CHANDELIER_ATR_MULT 倍 ATR」：只要創新高就有新的
            # 止損保護，回吐幅度用該幣種自己的 ATR 衡量，量小的幣（如 TRX）
            # 用小距離就會啟動保護，量大的幣（如 BANK）則有對應更大的空間，
            # 不用像百分比那樣一體適用卻對每個幣鬆緊不一。
            if old_sl > 0 and not is_flash:
                atr_val = meta.get("atr") or entry_p * 0.015
                chandelier_distance = CHANDELIER_ATR_MULT * atr_val
                # 保本鎖 + ATR 移動停利疊加：一旦最高價（多單）/最低價（空單）
                # 扣掉手續費+滑點緩衝後仍是淨賺，立刻把止損鎖到保本線——不用
                # 等 ATR 移動停利先跟上，避免「剛轉正就拉回，結果連保本都保
                # 不住」的空窗期。價格續創新高/新低時，ATR 移動停利接手繼續
                # 收緊；兩者取較嚴格的一個，只會愈收愈緊，不會放寬。
                if side == "LONG":
                    peak_price = meta["highest_price"]
                    breakeven_level = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                    candidates = [old_sl]
                    if peak_price >= breakeven_level:
                        candidates.append(breakeven_level)
                    if peak_price > entry_p:
                        candidates.append(peak_price - chandelier_distance)
                    trail_sl = max(candidates)
                    # 移動幅度太小就不重新掛單：避免行情緩慢推進時每一輪主迴圈
                    # (5秒)都在 cancel/create 保護單，頻繁觸發 API 呼叫/速率限制。
                    # 保本鎖第一次啟動時通常是相對原本寬止損的一大步，不會被這個
                    # 門檻擋住，只有後續的細微調整才會被跳過。
                    if trail_sl > old_sl and (trail_sl - old_sl) >= atr_val * 0.05:
                        new_sl = float(self.exchange.price_to_precision(symbol, trail_sl))
                        # 移動止盈：止損還在收緊，代表趨勢仍在走，固定止盈價不該
                        # 提前把單子封頂——用當下價格重新算 ATR 距離往外推，只會
                        # 往外擴不會往內縮，出場主要交給移動止損判斷趨勢是否反轉。
                        _, tp_distance = compute_sl_tp_distance(curr_p, atr_val)
                        new_tp = max(curr_p + tp_distance, meta.get("tp") or 0.0)
                        new_tp_price = float(self.exchange.price_to_precision(symbol, new_tp))
                        await self._replace_stop_order(
                            symbol, side, pos["qty"], old_sl, new_sl, meta, pos, new_tp=new_tp_price
                        )
                        if now_ts - self._last_trailing_log.get(symbol, 0) >= 30:
                            self._last_trailing_log[symbol] = now_ts
                            self.log(
                                f"📈 [ATR移動止利] {symbol} 最高價 {peak_price:.6f}，"
                                f"止損推至 {new_sl}，止盈同步推至 {new_tp_price}",
                                "SUCCESS",
                            )
                else:
                    trough_price = meta["lowest_price"]
                    breakeven_level = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                    candidates = [old_sl]
                    if trough_price <= breakeven_level:
                        candidates.append(breakeven_level)
                    if trough_price < entry_p:
                        candidates.append(trough_price + chandelier_distance)
                    trail_sl = min(candidates)
                    if trail_sl < old_sl and (old_sl - trail_sl) >= atr_val * 0.05:
                        new_sl = float(self.exchange.price_to_precision(symbol, trail_sl))
                        _, tp_distance = compute_sl_tp_distance(curr_p, atr_val)
                        current_tp = meta.get("tp") or float("inf")
                        new_tp = min(curr_p - tp_distance, current_tp)
                        new_tp_price = float(self.exchange.price_to_precision(symbol, new_tp))
                        await self._replace_stop_order(
                            symbol, side, pos["qty"], old_sl, new_sl, meta, pos, new_tp=new_tp_price
                        )
                        if now_ts - self._last_trailing_log.get(symbol, 0) >= 30:
                            self._last_trailing_log[symbol] = now_ts
                            self.log(
                                f"📉 [ATR移動止利] {symbol} 最低價 {trough_price:.6f}，"
                                f"止損推至 {new_sl}，止盈同步推至 {new_tp_price}",
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

    def _is_flash_move(self, symbol: str, curr_p: float, side: str, now_ts: float) -> bool:
        """短窗口內逆勢劇烈波動（急殺/急拉）偵測，只看最近 FLASH_MOVE_WINDOW_SEC 秒。"""
        history = self._price_history.setdefault(symbol, [])
        history.append((now_ts, curr_p))
        cutoff = now_ts - FLASH_MOVE_WINDOW_SEC
        while history and history[0][0] < cutoff:
            history.pop(0)
        if len(history) < 2:
            return False
        oldest_price = history[0][1]
        if oldest_price <= 0:
            return False
        if side == "LONG":
            adverse_move = (oldest_price - curr_p) / oldest_price
        else:
            adverse_move = (curr_p - oldest_price) / oldest_price
        return adverse_move >= FLASH_MOVE_THRESHOLD_PCT

    async def _replace_stop_order(
        self, symbol: str, side: str, qty: float, old_sl: float, new_sl: float, meta: dict, pos: dict,
        new_tp: float = None,
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
            if new_tp and new_tp > 0:
                meta["tp"] = new_tp
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
        sl_distance, tp_distance = compute_sl_tp_distance(entry_p, atr)
        if side == "LONG":
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p - sl_distance))
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p + tp_distance))
        else:
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p + sl_distance))
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p - tp_distance))
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
            # 強制逐倉保證金模式：專案從沒設定過保證金模式，代表可能一直用帳戶
            # 預設的全倉——全倉下一筆爆倉會吃掉整個帳戶保證金，不只該筆本金，
            # 直接打破 MAX_SLOTS/TRADE_AMOUNT_USDT「每筆只賭固定金額」的風控
            # 假設。已經是 ISOLATED 時 Binance 會回錯誤（-4046 No need to change
            # margin type），單純忽略即可，不影響下單流程。
            try:
                await self.exchange.set_margin_mode("ISOLATED", symbol)
            except Exception:
                pass
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
            atr_value = atr if atr > 0 else execution_price * 0.015
            # 進場緩衝期：剛進場的 ENTRY_GRACE_SECONDS 秒內，實際掛在交易所的
            # 止損先額外放寬 ENTRY_GRACE_EXTRA_ATR 倍 ATR，避開剛進場時最容易
            # 發生的 MARK_PRICE 瞬間偏離雜訊；緩衝期一過會自動收緊回 sl_price。
            grace_buffer = atr_value * ENTRY_GRACE_EXTRA_ATR
            grace_sl = (
                sl_price - grace_buffer if side == "LONG" else sl_price + grace_buffer
            )
            grace_sl_price = float(self.exchange.price_to_precision(symbol, grace_sl))
            try:
                await self._create_protection_order(
                    symbol, close_side, "STOP_MARKET", qty, grace_sl_price
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
                "sl": grace_sl_price,
                "target_sl": sl_price,
                "grace_until": time.time() + ENTRY_GRACE_SECONDS,
                "tp": tp_price,
                "atr": atr_value,
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
                f"{execution_price:.6f} ({leverage}x，SL={sl_price}, TP={tp_price}，"
                f"{ENTRY_GRACE_SECONDS:.0f}秒緩衝期內實際止損暫寬至 {grace_sl_price})",
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
