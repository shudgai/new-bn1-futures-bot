"""
Exchange Order Manager (交易所原生訂單與硬鎖利委託管理模組)
專門處理 1000PEPE、龍蝦等妖幣高波動標的的「交易所伺服器原生條件單 (Exchange Native Stop Order)」
徹底解決 Python 本體輪詢延遲與極速暴跌滑價問題，確保保底鎖利 100% 實質平倉落袋！
"""

import asyncio
import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("ExchangeOrderManager")


class ExchangeOrderManager:
    """
    交易所端硬性止盈/保本單管理器 (Exchange Native Hard Stop Order Manager)
    
    核心機制：
    1. 當持倉浮盈達到鎖利門檻 (例如 +8.8U 或階梯保本價) 時，立即調用交易所原生 API
       掛載 STOP_MARKET / STOP_LOSS_LIMIT 條件單 (reduceOnly=True)。
    2. 由幣安撮合引擎在行情觸及觸發價 (Trigger/Stop Price) 的第 1 毫秒自動撮合成交，
       100% 擺脫本地 Python 等待 1 秒輪詢或等 60 秒 K 線收線的滯後慘劇！
    3. 動態追蹤止盈 (Trailing Stop Limit)：當價格再創新高/新低時，自動取消舊單並重新掛上更新後的鎖利線。
    4. 滑價容忍保護 (Slippage Cap)：提供 0.1 * ATR 容忍範圍，保護鎖利實拿金額。
    """

    @classmethod
    async def place_or_update_native_stop_order(
        cls,
        account: Any,
        symbol: str,
        position: Dict[str, Any],
        target_stop_price: float,
        *,
        atr: Optional[float] = None,
        prefer_limit: bool = False,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        在交易所端掛載或移動原生止盈/止損單 (Native Stop Order)。
        
        :param account: BinanceTestnetAccount 或 PaperAccount
        :param symbol: 交易幣種 (例如 1000PEPE/USDT, 龙虾/USDT)
        :param position: 當前持倉 dict
        :param target_stop_price: 目標鎖利/防守價 (Stop Price)
        :param atr: 前根 ATR (若提供則用於計算防滑價 Limit 價格)
        :param prefer_limit: 是否使用 STOP (Limit) 防滑價，否則預設使用 STOP_MARKET
        """
        if not position or not target_stop_price or target_stop_price <= 0:
            return False, "INVALID_POSITION_OR_PRICE", {}

        side = str(position.get("side", "")).upper()
        if side not in ("LONG", "SHORT"):
            return False, "INVALID_SIDE", {}

        qty = float(position.get("qty", 0.0) or position.get("amount", 0.0) or 0.0)
        if qty <= 0:
            return False, "INVALID_QTY", {}

        close_side = "SELL" if side == "LONG" else "BUY"
        meta = getattr(account, "position_meta", {}).get(symbol, {})
        current_algo_id = meta.get("native_lock_algo_id") or position.get("native_lock_algo_id")
        current_lock_price = float(meta.get("native_locked_price", 0.0) or position.get("native_locked_price", 0.0) or 0.0)

        # 止盈線只進不退 (Monotonic Ladder Lock)
        if current_lock_price > 0:
            if side == "LONG" and target_stop_price <= current_lock_price + 1e-9:
                return False, "STOP_PRICE_NOT_IMPROVED", {"current": current_lock_price, "target": target_stop_price}
            if side == "SHORT" and target_stop_price >= current_lock_price - 1e-9:
                return False, "STOP_PRICE_NOT_IMPROVED", {"current": current_lock_price, "target": target_stop_price}

        # 格式化精度
        price_prec_func = getattr(account, "_price_to_precision", None)
        amt_prec_func = getattr(account, "_amount_to_precision", None)

        formatted_stop_price = float(price_prec_func(symbol, target_stop_price)) if callable(price_prec_func) else target_stop_price
        formatted_qty = float(amt_prec_func(symbol, qty)) if callable(amt_prec_func) else qty

        # 判斷是否為真實/測試網交易所 (Binance API) 還是本地紙上交易
        is_real_exchange = hasattr(account, "exchange") and getattr(account.exchange, "request", None) is not None

        details: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "close_side": close_side,
            "qty": formatted_qty,
            "stop_price": formatted_stop_price,
            "previous_stop_price": current_lock_price,
        }

        # ── 1. 真實/測試網交易所原生 API 掛單 ──
        if is_real_exchange:
            try:
                raw_sym_func = getattr(account, "_raw_symbol", lambda s: s.replace("/", ""))
                raw_sym = raw_sym_func(symbol)

                # A. 先撤銷舊的原生條件單
                if current_algo_id:
                    try:
                        await account.exchange.request(
                            "algoOrder",
                            "fapiPrivate",
                            "DELETE",
                            {"symbol": raw_sym, "algoId": current_algo_id},
                        )
                        logger.info(f"🗑️ [NATIVE_STOP_CANCEL] 成功取消舊鎖利單 algoId={current_algo_id}")
                    except Exception as cancel_err:
                        logger.warning(f"⚠️ [NATIVE_STOP_CANCEL_WARN] 取消舊單失敗或已成交: {cancel_err}")

                # B. 建立交易所原生條件單 (STOP_MARKET 或 STOP 限價防滑)
                params: Dict[str, Any] = {
                    "algoType": "CONDITIONAL",
                    "symbol": raw_sym,
                    "side": close_side,
                    "quantity": formatted_qty,
                    "triggerPrice": formatted_stop_price,
                    "reduceOnly": "true",
                    "workingType": "MARK_PRICE",  # 使用標記價格觸發，防止現貨插針惡意引爆
                }

                if prefer_limit and atr and atr > 0:
                    # 防滑價：掛出限價單，容許 0.1 ATR 滑價
                    slippage_buffer = 0.1 * atr
                    limit_price = (formatted_stop_price - slippage_buffer) if side == "LONG" else (formatted_stop_price + slippage_buffer)
                    limit_price = float(price_prec_func(symbol, limit_price)) if callable(price_prec_func) else limit_price
                    params["type"] = "STOP"
                    params["price"] = limit_price
                    details["order_type"] = "STOP_LIMIT"
                    details["limit_price"] = limit_price
                else:
                    params["type"] = "STOP_MARKET"
                    details["order_type"] = "STOP_MARKET"

                res = await account.exchange.request("algoOrder", "fapiPrivate", "POST", params)
                new_algo_id = res.get("algoId") or res.get("id") or str(res)
                details["algoId"] = new_algo_id

                logger.warning(
                    f"🛡️ [EXCHANGE_NATIVE_STOP_PLACED] {symbol} {side} 成功在幣安掛載原生硬鎖利單！"
                    f"StopPrice={formatted_stop_price}, Qty={formatted_qty}, AlgoID={new_algo_id}"
                )

                # 更新持倉狀態
                position["native_lock_algo_id"] = new_algo_id
                position["native_locked_price"] = formatted_stop_price
                meta["native_lock_algo_id"] = new_algo_id
                meta["native_locked_price"] = formatted_stop_price
                account.save_state()
                return True, "NATIVE_STOP_ORDER_PLACED", details

            except Exception as exc:
                logger.error(f"❌ [EXCHANGE_NATIVE_STOP_ERROR] 掛載原生條件單失敗 symbol={symbol}: {exc}")
                return False, f"EXCHANGE_ERROR: {exc}", details

        # ── 2. 本地紙上交易 (Paper Account) 模擬掛載硬鎖利線 ──
        else:
            try:
                # 更新紙上持倉的 sl (硬止損/鎖利價)
                position["sl"] = formatted_stop_price
                position["native_locked_price"] = formatted_stop_price
                position["is_breakeven_moved"] = True
                meta["sl"] = formatted_stop_price
                meta["native_locked_price"] = formatted_stop_price
                meta["is_breakeven_moved"] = True
                account.save_state()
                account.log(
                    f"🛡️ [PAPER_NATIVE_STOP_LOCKED] {symbol} {side} 成功掛載保底鎖利線！"
                    f"鎖利價={formatted_stop_price:.6g}, 數量={formatted_qty}",
                    "SUCCESS",
                )
                return True, "PAPER_NATIVE_STOP_PLACED", details
            except Exception as exc:
                logger.error(f"❌ [PAPER_NATIVE_STOP_ERROR] symbol={symbol}: {exc}")
                return False, f"PAPER_ERROR: {exc}", details

    @classmethod
    async def cancel_native_stop_order(cls, account: Any, symbol: str) -> bool:
        """持倉平倉或手動結束時，撤銷交易所端掛載的原生條件單"""
        try:
            meta = getattr(account, "position_meta", {}).get(symbol, {})
            algo_id = meta.get("native_lock_algo_id")
            if not algo_id:
                return True

            is_real = hasattr(account, "exchange") and getattr(account.exchange, "request", None) is not None
            if is_real:
                raw_sym_func = getattr(account, "_raw_symbol", lambda s: s.replace("/", ""))
                await account.exchange.request(
                    "algoOrder",
                    "fapiPrivate",
                    "DELETE",
                    {"symbol": raw_sym_func(symbol), "algoId": algo_id},
                )
                logger.info(f"🗑️ [NATIVE_STOP_CLEARED] {symbol} 撤銷原生條件單 algoId={algo_id}")

            meta.pop("native_lock_algo_id", None)
            meta.pop("native_locked_price", None)
            account.save_state()
            return True
        except Exception as exc:
            logger.warning(f"⚠️ [NATIVE_STOP_CLEAR_WARN] symbol={symbol} error={exc}")
            return False
