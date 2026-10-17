import asyncio
import os
import json
from typing import Dict, Any, Optional
from core.engine import engine
from core.config import DEFAULT_SYMBOLS, get_leverage, TAKER_FEE_RATE
from core.symbol_aliases import is_auto_entry_symbol
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
                    symbols = data
                elif isinstance(data, dict) and "symbols" in data:
                    symbols = data["symbols"]
                else:
                    symbols = []
                allowed = [
                    symbol for symbol in symbols
                    if is_auto_entry_symbol(symbol)
                ]
                return allowed or [
                    symbol for symbol in DEFAULT_SYMBOLS
                    if is_auto_entry_symbol(symbol)
                ]
        except Exception as e:
            logger.error(f"Failed to read symbol_selection.json: {e}")
    # Fallback to engine tickers if file not found or invalid
    return [
        symbol for symbol in engine.tickers.keys()
        if is_auto_entry_symbol(symbol)
    ] if hasattr(engine, 'tickers') else [
        symbol for symbol in DEFAULT_SYMBOLS if is_auto_entry_symbol(symbol)
    ]

async def process_manual_order(
    symbol: str,
    side: str,
    amount: float = 0.0,
    quantity: float = 0.0,
    price: Optional[str] = None
) -> Dict[str, Any]:
    
    async with _manual_order_lock:
        if not is_auto_entry_symbol(symbol):
            return {
                "success": False,
                "status_code": 403,
                "detail": "目前僅允許龍蝦 (LOBSTER/USDT) 與 CAP/USDT 開倉",
            }
        if symbol not in engine.tickers:
            return {"success": False, "status_code": 400, "detail": "Invalid Symbol: 幣種價格尚未載入"}

        live_price = float(engine.tickers[symbol])
        
        # Determine actual execution price
        exec_price = live_price
        if price and price.upper() != "MARKET":
            try:
                exec_price = float(price)
            except ValueError:
                return {"success": False, "status_code": 400, "detail": "Invalid Price Format"}

        leverage = get_leverage(symbol)
        available_balance = max(0.0, float(engine.account.get_available_balance()))
        max_margin = engine._full_wallet_entry_margin(
            float(engine.account.get_wallet_balance()),
            available_balance,
            leverage,
            symbol,
        )
        
        # Calculate amount (USDT) within the same wallet and slot caps as bot entries.
        if quantity > 0:
            target_amount = quantity * exec_price / leverage
        elif amount > 0:
            target_amount = amount
        else:
            target_amount = max_margin
            
        if target_amount <= 0:
            return {"success": False, "status_code": 400, "detail": "Insufficient Balance or invalid quantity/amount"}
        if not max_margin or target_amount > max_margin:
            return {
                "success": False,
                "status_code": 400,
                "detail": "Requested margin exceeds the account-balance risk limit",
            }

        # Check balance
        cost = target_amount * (1.0 + leverage * max(TAKER_FEE_RATE, 0.0))
        if cost > available_balance:
            return {"success": False, "status_code": 400, "detail": "Insufficient Balance"}

        # Get real ATR if available, else fallback
        atr = exec_price * 0.015
        if symbol in getattr(engine, "data_frames", {}):
            df = engine.data_frames[symbol]
            if not df.empty and "atr" in df.columns:
                atr = float(df["atr"].iloc[-1])

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
                "source": "MANUAL",
                "managed_by_bot": True,
                "manual_favorable_rail_reached": False,
                "channel_favorable_rail_reached": False,
                "entry_atr": atr,
            },
        )
        
        if not success:
            return {"success": False, "status_code": 400, "detail": "已有該幣種持倉或系統異常"}
            
        engine.release_manual_close_state(symbol)
        engine._take_over_manual_position(symbol, engine.account.positions[symbol])
        
        return {"success": True, "message": f"手動開倉 {side} {symbol} 成功"}
