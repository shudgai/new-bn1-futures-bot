"""
core/services/execution_manager.py
==================================
三層流水線之【執行層 (Execution Layer，唯一通道)】
職責：
- 全專案唯一合法的開平倉下單執行器。
- 嚴格封閉外部任意模組直接呼叫交易所下單 API。
- 主循環每秒跑標準流程：
  1. 抓取數據與計算指標 (DataPipeline)
  2. 若有持倉：只問 ExitGate -> 若觸發則呼叫 broker.close_position
  3. 若無持倉：只問 EntryGate -> 若通過則呼叫 broker.open_position
"""
import logging
from typing import Dict, Any, Optional
import pandas as pd

from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.gates.unified_pipeline_gates import ExitGate, EntryGate

logger = logging.getLogger("ExecutionManager")


class ExecutionManager:
    """唯一開平倉執行通道與標準流水線協調器"""

    @classmethod
    async def process_symbol_pipeline(
        cls,
        symbol: str,
        engine_or_account: Any,
        klines_df: Optional[pd.DataFrame] = None,
        quote: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        單一幣種的標準流水線處理循環 (Thread-safe, Single-Pipeline)
        """
        account = getattr(engine_or_account, "account", engine_or_account)

        # 1. 抓取數據與計算純數值指標 (Data Layer)
        current_position = account.positions.get(symbol)
        has_position = bool(current_position and abs(float(current_position.get("amount", 0.0) or current_position.get("qty", 0.0))) > 0)

        if klines_df is None or klines_df.empty:
            if hasattr(engine_or_account, "fetch_klines"):
                try:
                    klines_df = await engine_or_account.fetch_klines(symbol, timeframe="1m", limit=60)
                except Exception as e:
                    logger.debug(f"[PIPELINE] {symbol} fetch_klines 失敗: {e}")
                    return {"status": "FETCH_FAILED", "symbol": symbol}
            else:
                return {"status": "NO_KLINES", "symbol": symbol}

        market_data: Optional[MarketDataSnapshot] = DataPipeline.calculate_snapshot(
            symbol=symbol,
            df=klines_df,
            current_price=quote,
            position=current_position if has_position else None,
        )
        if not market_data:
            return {"status": "SNAPSHOT_FAILED", "symbol": symbol}

        # 2. 若有持倉：只問 ExitGate (唯一裁決官)
        if has_position:
            should_exit, reason, meta = ExitGate.evaluate(
                pos=current_position,
                market_data=market_data,
            )
            if should_exit:
                logger.info(f"🚀 [EXIT_TRIGGERED] {symbol} {reason} | meta={meta}")
                print(f"🚀 [EXIT_TRIGGERED] {symbol} {reason}", flush=True)
                exit_price = market_data.current_price
                success = await cls.close_position(
                    account=account,
                    symbol=symbol,
                    price=exit_price,
                    reason=f"ExitGate:{reason}",
                    meta=meta,
                )
                return {"status": "EXIT_EXECUTED", "symbol": symbol, "reason": reason, "success": success}
            else:
                logger.debug(f"🛡️ [HOLD] {symbol} {reason}")
                return {"status": "HOLD", "symbol": symbol, "reason": reason}

        # 3. 若無持倉：只問 EntryGate (唯一裁決官)
        else:
            # 偵測候選方向訊號 (快車道突破或均線方向)
            candidate_signal = None
            if market_data.current_price > market_data.kc_upper or (market_data.curr_k["close"] > market_data.kc_middle and market_data.ma5 > market_data.ma15):
                candidate_signal = "BREAKOUT_LONG"
            elif market_data.current_price < market_data.kc_lower or (market_data.curr_k["close"] < market_data.kc_middle and market_data.ma5 < market_data.ma15):
                candidate_signal = "BREAKDOWN_SHORT"

            if candidate_signal:
                should_entry, reason, meta = EntryGate.evaluate(
                    signal_type=candidate_signal,
                    market_data=market_data,
                )
                if should_entry:
                    side = meta.get("side", "LONG" if "LONG" in candidate_signal else "SHORT")
                    logger.info(f"🎯 [ENTRY_TRIGGERED] {symbol} {side} {reason} | meta={meta}")
                    print(f"🎯 [ENTRY_TRIGGERED] {symbol} {side} {reason}", flush=True)
                    success = await cls.open_position(
                        engine_or_account=engine_or_account,
                        symbol=symbol,
                        side=side,
                        price=market_data.current_price,
                        reason=f"EntryGate:{reason}",
                        meta=meta,
                    )
                    return {"status": "ENTRY_EXECUTED", "symbol": symbol, "side": side, "reason": reason, "success": success}
                else:
                    logger.debug(f"🚫 [VETO] {symbol} {reason}")
                    return {"status": "VETO", "symbol": symbol, "reason": reason}

        return {"status": "IDLE", "symbol": symbol}

    @classmethod
    async def close_position(
        cls,
        account: Any,
        symbol: str,
        price: float,
        reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """全系統唯一平倉執行管道"""
        try:
            if hasattr(account, "close_position"):
                return await account.close_position(symbol=symbol, current_price=price, close_reason=reason)
            return False
        except Exception as exc:
            logger.error(f"[EXECUTION] close_position 異常 {symbol}: {exc}")
            return False

    @classmethod
    async def open_position(
        cls,
        engine_or_account: Any,
        symbol: str,
        side: str,
        price: float,
        reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """全系統唯一開倉執行管道"""
        try:
            if hasattr(engine_or_account, "execute_direct_entry"):
                return await engine_or_account.execute_direct_entry(
                    symbol=symbol,
                    side=side,
                    quote=price,
                    entry_mode="WILD_SWING",
                    meta_dict=meta or {},
                )
            account = getattr(engine_or_account, "account", engine_or_account)
            if hasattr(account, "open_position"):
                return await account.open_position(
                    symbol=symbol,
                    side=side,
                    price=price,
                    reason=reason,
                )
            return False
        except Exception as exc:
            logger.error(f"[EXECUTION] open_position 異常 {symbol}: {exc}")
            return False
