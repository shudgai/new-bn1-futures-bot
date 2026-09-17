import asyncio
import os
import json
from typing import Dict, Any, Optional
from core.engine import engine
from core.config import get_leverage, TAKER_FEE_RATE
from core.strategy import compute_sl_tp_distance, build_sl_tp_for_side
import logging

logger = logging.getLogger(__name__)

# Atomic lock for order placement to prevent race conditions
_manual_order_lock = asyncio.Lock()

def get_symbol_selection() -> list:
    filepath = "data/symbol_selection.json"
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "symbols" in data:
                    return data["symbols"]
        except Exception as e:
            logger.error(f"Failed to read symbol_selection.json: {e}")
    # Fallback to engine tickers if file not found or invalid
    return list(engine.tickers.keys()) if hasattr(engine, 'tickers') else []

async def process_manual_order(
    symbol: str,
    side: str,
    amount: float = 0.0,
    quantity: float = 0.0,
    price: Optional[str] = None
) -> Dict[str, Any]:
    
    async with _manual_order_lock:
        if symbol not in engine.tickers:
            return {"success": False, "status_code": 400, "detail": "Invalid Symbol: 幣種價格尚未載入"}
            
        if symbol in engine.account.positions:
            return {"success": False, "status_code": 400, "detail": f"{symbol} 已有持倉"}

        live_price = float(engine.tickers[symbol])
        
        # Determine actual execution price
        exec_price = live_price
        if price and price.upper() != "MARKET":
            try:
                exec_price = float(price)
            except ValueError:
                return {"success": False, "status_code": 400, "detail": "Invalid Price Format"}

        leverage = get_leverage(symbol)
        
        # Calculate amount (USDT)
        if quantity > 0:
            target_amount = quantity * exec_price / leverage
        elif amount > 0:
            target_amount = amount
        else:
            slot_amount = max(0.0, float(engine._continuous_entry_amount()))
            available = max(0.0, float(engine.account.get_available_balance()))
            fee_safe_available = available / (1.0 + leverage * max(TAKER_FEE_RATE, 0.0))
            target_amount = min(slot_amount, fee_safe_available)
            
        if target_amount <= 0:
            return {"success": False, "status_code": 400, "detail": "Insufficient Balance or invalid quantity/amount"}

        # Check balance
        available_balance = max(0.0, float(engine.account.get_available_balance()))
        cost = target_amount * (1.0 + leverage * max(TAKER_FEE_RATE, 0.0))
        if cost > available_balance:
            return {"success": False, "status_code": 400, "detail": "Insufficient Balance"}

        atr = exec_price * 0.015  # Fallback manual ATR
        sl_dist, tp_dist = compute_sl_tp_distance(exec_price, atr)
        sl, tp = build_sl_tp_for_side(exec_price, side, sl_dist, tp_dist)

        success = await engine.account.open_position(
            symbol=symbol,
            side=side,
            price=exec_price,
            amount_usdt=target_amount,
            sl=sl,
            tp=tp,
            reason=f"手動開倉_{side}",
            atr=atr,
            leverage=leverage,
            signal_score=100,
            entry_context={
                "entry_mode": "CHANNEL_SWING",
                "wave_regime": "RANGE",
                "market_mode": "RANGE",
                "manual_entry": True,
                "managed_by_bot": True,
                "manual_favorable_rail_reached": False,
                "channel_favorable_rail_reached": False,
            },
        )
        
        if not success:
            return {"success": False, "status_code": 400, "detail": "已有該幣種持倉或系統異常"}
            
        engine.release_manual_close_state(symbol)
        engine._take_over_manual_position(symbol, engine.account.positions[symbol])
        
        return {"success": True, "message": f"手動開倉 {side} {symbol} 成功"}
