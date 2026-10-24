"""
全鏈路訂單審查與生命週期日誌 (OrderAuditTrail)
Path: core/services/order_audit_trail.py

提供訂單從「訊號產生 -> EntryGate -> 下單入口 -> 交易所回報 -> 持倉同步」
5 大階段的全局 signal_id 追蹤與結構化日誌記錄，
確保任何違規開倉或拒單均可一鍵回溯溯源。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("OrderAuditTrail")

AUDIT_LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "logs",
    "order_audit_trail.jsonl",
)


class OrderAuditTrail:
    @staticmethod
    def _write_audit_log(entry: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(AUDIT_LOG_FILE), exist_ok=True)
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error(f"Failed to write audit log: {exc}")

    @classmethod
    def record_stage(
        cls,
        stage: str,
        symbol: str,
        side: str,
        signal_id: str,
        data: Dict[str, Any],
        account: Any = None,
    ) -> None:
        """
        記錄指定階段的審計資訊
        stage:
          1. SIGNAL_GEN
          2. ENTRY_GATE_EVAL
          3. ORDER_DISPATCH
          4. EXCHANGE_RETURN
          5. POSITION_SYNC
        """
        entry = {
            "timestamp": time.time(),
            "iso_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "stage": stage,
            "signal_id": signal_id,
            "symbol": symbol,
            "side": side,
            "data": data,
        }
        cls._write_audit_log(entry)

        # 格式化輸出到系統 logger 或 account.log
        msg = f"🔍 [AUDIT:{stage}] {symbol} {side} | sig_id={signal_id} | {json.dumps(data, ensure_ascii=False)}"
        if account is not None and hasattr(account, "log"):
            level = "WARNING" if data.get("status") in ("VETO", "REJECTED", "FAIL") else "INFO"
            account.log(msg, level)
        else:
            logger.info(msg)

    @classmethod
    def record_signal_generated(
        cls,
        symbol: str,
        side: str,
        price: float,
        source: str,
        bar_timestamp: float,
        signal_id: str,
        account: Any = None,
    ) -> None:
        cls.record_stage(
            stage="1_SIGNAL_GEN",
            symbol=symbol,
            side=side,
            signal_id=signal_id,
            data={
                "source": source,
                "price": price,
                "bar_timestamp": bar_timestamp,
            },
            account=account,
        )

    @classmethod
    def record_entry_gate_eval(
        cls,
        symbol: str,
        side: str,
        signal_id: str,
        gate_result: Any,
        account: Any = None,
    ) -> None:
        cls.record_stage(
            stage="2_ENTRY_GATE_EVAL",
            symbol=symbol,
            side=side,
            signal_id=signal_id,
            data={
                "passed": getattr(gate_result, "passed", False),
                "reason": getattr(gate_result, "reason", "UNKNOWN"),
                "checks": getattr(gate_result, "checks", {}),
            },
            account=account,
        )

    @classmethod
    def record_order_dispatch(
        cls,
        symbol: str,
        side: str,
        signal_id: str,
        amount_usdt: float,
        leverage: int,
        quote_price: float,
        in_position: bool,
        account: Any = None,
    ) -> None:
        cls.record_stage(
            stage="3_ORDER_DISPATCH",
            symbol=symbol,
            side=side,
            signal_id=signal_id,
            data={
                "amount_usdt": amount_usdt,
                "leverage": leverage,
                "quote_price": quote_price,
                "account_in_position": in_position,
                "revalidated": True,
            },
            account=account,
        )

    @classmethod
    def record_exchange_return(
        cls,
        symbol: str,
        side: str,
        signal_id: str,
        order_id: Optional[str],
        status: str,
        filled_qty: float,
        avg_price: float,
        account: Any = None,
    ) -> None:
        cls.record_stage(
            stage="4_EXCHANGE_RETURN",
            symbol=symbol,
            side=side,
            signal_id=signal_id,
            data={
                "order_id": str(order_id or ""),
                "status": status,
                "filled_qty": filled_qty,
                "avg_price": avg_price,
            },
            account=account,
        )

    @classmethod
    def record_position_sync(
        cls,
        symbol: str,
        side: str,
        signal_id: str,
        actual_positions: Dict[str, Any],
        account: Any = None,
    ) -> None:
        cls.record_stage(
            stage="5_POSITION_SYNC",
            symbol=symbol,
            side=side,
            signal_id=signal_id,
            data={
                "has_position": symbol in actual_positions,
                "position_details": actual_positions.get(symbol, {}),
            },
            account=account,
        )
