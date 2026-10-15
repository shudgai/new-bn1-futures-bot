"""RatchetNetProfitManager / Gate
徹底解決「14 滑到 11」慘劇，順利切換為「5% 淨利起跑、回吐 3%、每階 3% 推進」鎖利邏輯。
包含：
1. 廢棄市價單平倉，改為帶微幅折讓（0.25% Offset）的限價防滑平倉單（Limit Order with offset），死守底線。
2. 純淨利計算：嚴格扣除手續費（開+平雙邊約 0.1%）與累計資金費率。
3. 階梯推進：
   - 最高淨利達 5% -> 鎖定 2% 淨利 (回吐 3%)
   - 最高淨利達 8% -> 鎖定 5% 淨利
   - 最高淨利達 11% -> 鎖定 8% 淨利（每加 3% 鎖利推 3%）
4. 掛單未完全成交逾時保護（Timeout Watchdog）：3 秒內未成交則微調追最佳盤口價。
"""
import time
import math
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class RatchetNetProfitManager:
    def __init__(self, exchange=None, symbol: str = "", fee_rate: float = 0.001, slippage_offset: float = 0.0025):
        """
        :param exchange: ccxt exchange 實例或交易引擎帳戶
        :param symbol: 交易幣種 (例如 'LOBSTER/USDT:USDT' 或 '1000PEPE/USDT')
        :param fee_rate: 預估雙邊手續費率 (預設 0.1% = 0.001)
        :param slippage_offset: 限價平倉的容忍折讓空間 (預設 0.25% = 0.0025，避免穿價未成交但死守底線)
        """
        self.exchange = exchange
        self.symbol = symbol
        self.fee_rate = fee_rate
        self.slippage_offset = slippage_offset

        # 鎖利參數：5% 淨利起跑，回吐 3%，每階段 3%
        self.start_net_profit = 0.05    # 5% 淨利起跑
        self.pullback = 0.03            # 回吐 3%
        self.step_size = 0.03           # 每階段 3%

        # 狀態紀錄 (開新倉時需重置)
        self.reset()

    def reset(self):
        """重置持倉狀態"""
        self.max_net_pnl = 0.0          # 歷史最高淨利潤率
        self.locked_net_pnl = 0.0       # 當前保底淨利潤率
        self.tier = 0                   # 當前階梯 (0: 未啟動, 1: 5%鎖2%, 2: 8%鎖5%, 3: 11%鎖8%...)
        self.active_order_id = None     # 當前掛出的平倉單 ID

    def calculate_net_pnl(self, side: str, entry_price: float, current_price: float, accumulated_funding_pct: float = 0.0) -> float:
        """精準計算當前淨利百分比（扣除雙邊手續費與累計資金費）"""
        if entry_price <= 0 or current_price <= 0:
            return 0.0
        side_lower = str(side).lower()
        if side_lower in ('long', 'buy'):
            gross_pnl = (current_price - entry_price) / entry_price
        else: # short, sell
            gross_pnl = (entry_price - current_price) / entry_price

        net_pnl = round(gross_pnl - self.fee_rate - accumulated_funding_pct, 6)
        return net_pnl

    def calculate_protected_limit_price(self, side: str, entry_price: float, accumulated_funding_pct: float = 0.0) -> float:
        """計算嚴格保證淨利所需的限價保護價格（帶微幅折讓空間但死守底線）"""
        side_lower = str(side).lower()
        if side_lower in ('long', 'buy'):
            # 多單平倉賣出：基礎價格保證鎖定淨利 + 手續費
            base_price = entry_price * (1.0 + self.locked_net_pnl + self.fee_rate + accumulated_funding_pct)
            # 限價賣單稍微往下折讓 0.25% 換取即時成交，但絕對死守在底線之上，絕不會被賤賣滑穿
            limit_price = base_price * (1.0 - self.slippage_offset)
        else:
            # 空單平倉買入：基礎價格保證鎖定淨利 - 手續費
            base_price = entry_price * (1.0 - self.locked_net_pnl - self.fee_rate - accumulated_funding_pct)
            # 限價買單稍微往上折讓 0.25% 換取即時撮合，但死守上限
            limit_price = base_price * (1.0 + self.slippage_offset)
        return limit_price

    def evaluate_and_update(
        self,
        side: str,
        entry_price: float,
        current_price: float,
        position_amount: float = 0.0,
        accumulated_funding_pct: float = 0.0,
        execute_close: bool = False,
        is_candle_closed: bool = True,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        每次價格更新時呼叫
        :return: (is_triggered: bool, details: dict)
        """
        current_net_pnl = self.calculate_net_pnl(side, entry_price, current_price, accumulated_funding_pct)

        # 1. 更新持倉期間最高淨利
        if current_net_pnl > self.max_net_pnl:
            self.max_net_pnl = current_net_pnl

        # 2. 計算與推進階梯 (5% 起跑，每 3% 一階，帶微小浮點容差)
        if self.max_net_pnl >= (self.start_net_profit - 1e-6):
            tier = int(round((self.max_net_pnl - self.start_net_profit + 1e-6) // self.step_size))
            target_lock = round((self.start_net_profit - self.pullback) + (tier * self.step_size), 6)

            # 單向棘輪：鎖定淨利只升不降
            if target_lock > self.locked_net_pnl:
                self.locked_net_pnl = target_lock
                self.tier = tier + 1
                msg = (f"[{self.symbol}] 達到新鎖利階梯 (Tier {self.tier}) | "
                       f"最高淨利: {self.max_net_pnl*100:.2f}% | 鎖定淨利線推至: {self.locked_net_pnl*100:.2f}%")
                logger.info(msg)
                print(msg, flush=True)

        details = {
            'max_net_pnl': self.max_net_pnl,
            'locked_net_pnl': self.locked_net_pnl,
            'current_net_pnl': current_net_pnl,
            'tier': self.tier,
            'armed': self.locked_net_pnl > 0,
            'is_candle_closed': is_candle_closed,
        }

        # 3. 檢查是否觸發回吐鎖利線
        if self.locked_net_pnl > 0 and current_net_pnl <= self.locked_net_pnl:
            # 防線一：收盤確認（Candle Close Only）
            # 只有當 K 棒真正走完收盤 (is_candle_closed == True) 時，才去結算當前淨利有沒有跌破鎖利線
            if not is_candle_closed:
                details['blocked_reason'] = "HOLD: CANDLE_NOT_CLOSED_PROFIT_LOCK_SHIELD"
                return False, details

            limit_price = self.calculate_protected_limit_price(side, entry_price, accumulated_funding_pct)
            details['limit_price'] = limit_price
            details['is_limit'] = True
            msg = (f"[{self.symbol}] 觸發鎖利！收盤確認淨利 {current_net_pnl*100:.2f}% <= 鎖利線 {self.locked_net_pnl*100:.2f}% | "
                   f"防滑限價: {limit_price:.6g}")
            logger.warning(msg)
            print(msg, flush=True)

            if execute_close and self.exchange is not None and position_amount > 0:
                self.execute_protected_limit_close(side, entry_price, position_amount, accumulated_funding_pct)

            return True, details

        return False, details

    def execute_protected_limit_close(self, side: str, entry_price: float, position_amount: float, accumulated_funding_pct: float = 0.0):
        """執行帶滑價保護的限價平倉（同步調用）"""
        close_side = 'sell' if str(side).lower() in ('long', 'buy') else 'buy'
        limit_price = self.calculate_protected_limit_price(side, entry_price, accumulated_funding_pct)

        # 格式化價格精度
        try:
            if hasattr(self.exchange, 'price_to_precision'):
                limit_price = float(self.exchange.price_to_precision(self.symbol, limit_price))
            if hasattr(self.exchange, 'amount_to_precision'):
                amount = float(self.exchange.amount_to_precision(self.symbol, abs(position_amount)))
            else:
                amount = abs(position_amount)
        except Exception:
            amount = abs(position_amount)

        print(f"發送限價防滑平倉單: 方向={close_side}, 數量={amount}, 限價={limit_price}", flush=True)

        try:
            params = {'reduceOnly': True}
            if hasattr(self.exchange, 'create_order'):
                order = self.exchange.create_order(
                    symbol=self.symbol,
                    type='limit',
                    side=close_side,
                    amount=amount,
                    price=limit_price,
                    params=params
                )
                self.active_order_id = order.get('id')
                # 啟動保護監控：確保單子在 3 秒內完全成交
                self._ensure_order_filled(self.active_order_id, close_side, amount)
        except Exception as e:
            print(f"下單異常: {e}", flush=True)

    def _ensure_order_filled(self, order_id: str, side: str, amount: float, timeout_sec: float = 3.0):
        """逾時監控：若 3 秒內未完全成交，進行盤口追價或應變"""
        if not order_id or not self.exchange:
            return
        if hasattr(self.exchange, '__class__') and 'Account' in self.exchange.__class__.__name__:
            ex = getattr(self.exchange, 'exchange', self.exchange)
        else:
            ex = self.exchange
        start_time = time.time()
        sleep_interval = min(0.5, max(0.01, timeout_sec / 4))
        while time.time() - start_time < timeout_sec:
            try:
                order_info = ex.fetch_order(order_id, self.symbol)
                if order_info.get('status') == 'closed':
                    print("鎖利限價單已完全成交！", flush=True)
                    self.reset()
                    return
            except Exception:
                pass
            time.sleep(sleep_interval)

        # 超過 3 秒仍未全部成交
        try:
            order_info = ex.fetch_order(order_id, self.symbol)
            remaining = float(order_info.get('remaining', 0.0))
            if remaining > 0:
                print(f"限價單未完全成交，剩餘數量: {remaining}，撤銷原單並重新追當前最佳買一/賣一家...", flush=True)
                ex.cancel_order(order_id, self.symbol)
                order_book = ex.fetch_order_book(self.symbol, limit=5)
                new_price = order_book['bids'][0][0] if side == 'sell' else order_book['asks'][0][0]
                if hasattr(ex, 'price_to_precision'):
                    new_price = float(ex.price_to_precision(self.symbol, new_price))
                ex.create_order(
                    symbol=self.symbol,
                    type='limit',
                    side=side,
                    amount=remaining,
                    price=new_price,
                    params={'reduceOnly': True}
                )
        except Exception as e:
            print(f"Watchdog 追單異常: {e}", flush=True)

    async def execute_protected_limit_close_async(
        self, side: str, entry_price: float, position_amount: float, accumulated_funding_pct: float = 0.0
    ):
        """非同步版本的限價防滑平倉與 3 秒逾時 Watchdog"""
        close_side = 'sell' if str(side).lower() in ('long', 'buy') else 'buy'
        limit_price = self.calculate_protected_limit_price(side, entry_price, accumulated_funding_pct)
        if hasattr(self.exchange, '__class__') and 'Account' in self.exchange.__class__.__name__:
            ex = getattr(self.exchange, 'exchange', self.exchange)
        else:
            ex = self.exchange

        try:
            if hasattr(ex, 'price_to_precision'):
                limit_price = float(ex.price_to_precision(self.symbol, limit_price))
            if hasattr(ex, 'amount_to_precision'):
                amount = float(ex.amount_to_precision(self.symbol, abs(position_amount)))
            else:
                amount = abs(position_amount)
        except Exception:
            amount = abs(position_amount)

        print(f"[Async] 發送限價防滑平倉單: 方向={close_side}, 數量={amount}, 限價={limit_price}", flush=True)
        try:
            params = {'reduceOnly': True}
            if hasattr(self.exchange, '_send_order'):
                # 專案內部 testnet_account / paper_account 接口
                order = await self.exchange._send_order(
                    self.symbol,
                    "limit",
                    close_side,
                    amount,
                    limit_price,
                    params,
                )
                self.active_order_id = (order or {}).get('id') or (order or {}).get('orderId')
                await self._ensure_order_filled_async(self.active_order_id, close_side, amount, limit_price)
            elif hasattr(ex, 'create_order'):
                res = ex.create_order(
                    symbol=self.symbol,
                    type='limit',
                    side=close_side,
                    amount=amount,
                    price=limit_price,
                    params=params,
                )
                order = await res if asyncio.iscoroutine(res) else res
                self.active_order_id = (order or {}).get('id')
                await self._ensure_order_filled_async(self.active_order_id, close_side, amount, limit_price)
        except Exception as e:
            print(f"[Async] 下單異常: {e}", flush=True)

    async def _ensure_order_filled_async(self, order_id: str, side: str, amount: float, current_limit: float, timeout_sec: float = 3.0):
        """非同步逾時監控：3 秒內檢查，未成交則改掛最佳盤口價"""
        if not order_id or not self.exchange:
            return
        if hasattr(self.exchange, '__class__') and 'Account' in self.exchange.__class__.__name__:
            ex = getattr(self.exchange, 'exchange', self.exchange)
        else:
            ex = self.exchange
        start_time = time.time()
        sleep_interval = min(0.5, max(0.01, timeout_sec / 4))
        while time.time() - start_time < timeout_sec:
            try:
                if hasattr(ex, 'fetch_order'):
                    res = ex.fetch_order(order_id, self.symbol)
                    order_info = await res if asyncio.iscoroutine(res) else res
                    if (order_info or {}).get('status') == 'closed':
                        print("[Async] 鎖利限價單已完全成交！", flush=True)
                        self.reset()
                        return
            except Exception:
                pass
            await asyncio.sleep(sleep_interval)

        # 3 秒逾時檢查
        try:
            if hasattr(ex, 'fetch_order'):
                res = ex.fetch_order(order_id, self.symbol)
                order_info = await res if asyncio.iscoroutine(res) else res
                remaining = float((order_info or {}).get('remaining', 0.0))
                if remaining > 0:
                    print(f"[Async] 限價單未完全成交，剩餘: {remaining}，撤銷並改追最佳買一/賣一價...", flush=True)
                    if hasattr(ex, 'cancel_order'):
                        c_res = ex.cancel_order(order_id, self.symbol)
                        if asyncio.iscoroutine(c_res):
                            await c_res
                    if hasattr(ex, 'fetch_order_book'):
                        ob_res = ex.fetch_order_book(self.symbol, limit=5)
                        order_book = await ob_res if asyncio.iscoroutine(ob_res) else ob_res
                        new_price = order_book['bids'][0][0] if side == 'sell' else order_book['asks'][0][0]
                        if hasattr(ex, 'price_to_precision'):
                            new_price = float(ex.price_to_precision(self.symbol, new_price))
                        if hasattr(ex, 'create_order'):
                            o_res = ex.create_order(
                                symbol=self.symbol,
                                type='limit',
                                side=side,
                                amount=remaining,
                                price=new_price,
                                params={'reduceOnly': True}
                            )
                            if asyncio.iscoroutine(o_res):
                                await o_res
        except Exception as e:
            print(f"[Async] Watchdog 追單異常: {e}", flush=True)


# 全域單例字典（依 symbol 隔離）
_symbol_ratchet_managers: Dict[str, RatchetNetProfitManager] = {}

def get_ratchet_manager(symbol: str, exchange=None, fee_rate: float = 0.001, slippage_offset: float = 0.0025) -> RatchetNetProfitManager:
    if symbol not in _symbol_ratchet_managers:
        _symbol_ratchet_managers[symbol] = RatchetNetProfitManager(
            exchange=exchange, symbol=symbol, fee_rate=fee_rate, slippage_offset=slippage_offset
        )
    return _symbol_ratchet_managers[symbol]
