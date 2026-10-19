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
        from core.symbol_aliases import is_lobster_symbol, is_cap_symbol
        if not is_auto_entry_symbol(symbol):
            return {
                "success": False,
                "status_code": 403,
                "detail": "目前僅允許龍蝦 (龙虾/USDT) 與 CAP/USDT 開倉",
            }

        canonical_symbol = "龙虾/USDT" if is_lobster_symbol(symbol) else ("CAP/USDT" if is_cap_symbol(symbol) else symbol)
        aliases = [symbol, canonical_symbol]
        if is_lobster_symbol(symbol):
            aliases.extend(['龙虾/USDT', 'LOBSTER/USDT', '龙虾/USDT:USDT', 'LOBSTER/USDT:USDT'])
        elif is_cap_symbol(symbol):
            aliases.extend(['CAP/USDT', 'CAP/USDT:USDT'])

        live_price = None
        for a in aliases:
            if a in getattr(engine, 'tickers', {}):
                try:
                    p = float(engine.tickers[a])
                    if p > 0:
                        live_price = p
                        break
                except (ValueError, TypeError):
                    pass

        if live_price is None or live_price <= 0:
            return {"success": False, "status_code": 400, "detail": "Invalid Symbol: 幣種價格尚未載入"}

        # Determine actual execution price
        exec_price = live_price
        if price and price.upper() != "MARKET":
            try:
                exec_price = float(price)
            except ValueError:
                return {"success": False, "status_code": 400, "detail": "Invalid Price Format"}

        leverage = get_leverage(canonical_symbol)
        available_balance = max(0.0, float(engine.account.get_available_balance()))
        wallet_balance = float(engine.account.get_wallet_balance())
        max_margin = engine._full_wallet_entry_margin(
            wallet_balance,
            available_balance,
            leverage,
            canonical_symbol,
        )
        if max_margin <= 0 and available_balance >= 5.0:
            max_margin = min(wallet_balance * 0.5, available_balance * 0.95)

        # Calculate amount (USDT) within the same wallet and slot caps as bot entries.
        if quantity > 0:
            target_amount = quantity * exec_price / leverage
        elif amount > 0:
            target_amount = amount
        else:
            target_amount = max_margin

        if target_amount <= 0:
            return {"success": False, "status_code": 400, "detail": "Insufficient Balance or invalid quantity/amount"}
        if max_margin and max_margin > 0 and target_amount > max_margin:
            target_amount = max_margin

        # Check balance
        cost = target_amount * (1.0 + leverage * max(TAKER_FEE_RATE, 0.0))
        if cost > available_balance:
            return {"success": False, "status_code": 400, "detail": "Insufficient Balance"}

        # Retrieve market frame
        entry_gate_frame = None
        for a in aliases:
            entry_gate_frame = getattr(engine, "data_frames", {}).get(a)
            if entry_gate_frame is not None and not getattr(entry_gate_frame, "empty", True):
                break
        if entry_gate_frame is None or getattr(entry_gate_frame, "empty", True):
            from core.services.entry_finality import fetch_settled_entry_frame
            for a in aliases:
                try:
                    entry_gate_frame = await fetch_settled_entry_frame(engine, a)
                    if entry_gate_frame is not None and not getattr(entry_gate_frame, "empty", True):
                        break
                except Exception:
                    pass
        if entry_gate_frame is None or getattr(entry_gate_frame, "empty", True):
            provider = getattr(engine.account, "entry_frame_provider", None)
            if callable(provider):
                for a in aliases:
                    try:
                        entry_gate_frame = await provider(a)
                        if entry_gate_frame is not None and not getattr(entry_gate_frame, "empty", True):
                            break
                    except Exception:
                        pass
        if entry_gate_frame is None or getattr(entry_gate_frame, "empty", True):
            for a in aliases:
                try:
                    entry_gate_frame = await engine.fetch_klines(a, timeframe='1m', limit=100, keep_live=True)
                    if entry_gate_frame is not None and not getattr(entry_gate_frame, "empty", True):
                        break
                except Exception:
                    pass
        if entry_gate_frame is None or getattr(entry_gate_frame, "empty", True):
            return {
                "success": False,
                "status_code": 400,
                "detail": "MA5/MA15 entry gate unavailable: market data is not ready",
            }

        required_cols = {"ma5", "ma15", "kc_upper", "kc_middle", "kc_lower", "atr"}
        if not required_cols.issubset(entry_gate_frame.columns):
            from core.services.technical_indicators import compute_technical_indicators
            try:
                entry_gate_frame = compute_technical_indicators(
                    entry_gate_frame,
                    atr_period=14,
                    atr_multiplier=2.0,
                    adx_period=14,
                    keltner_atr_multiplier=1.5,
                )
            except Exception as e:
                logger.warning(f"Failed to enrich technical indicators for manual order: {e}")

        # Get real ATR if available, else fallback
        atr = exec_price * 0.015
        if symbol in getattr(engine, "data_frames", {}):
            df = engine.data_frames[symbol]
            if not df.empty and "atr" in df.columns:
                atr = float(df["atr"].iloc[-1])

        sl_dist, tp_dist = compute_sl_tp_distance(exec_price, atr)
        sl, tp = build_sl_tp_for_side(exec_price, side, sl_dist, tp_dist)

        try:
            success = await engine.account.open_position(
                symbol=canonical_symbol,
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
                    "entry_gate_frame": entry_gate_frame,
                    "entry_gate_quote": live_price,
                },
            )
        except Exception as e:
            logger.warning(f"Manual open_position error: {e}")
            return {"success": False, "status_code": 400, "detail": f"開倉攔截: {e}"}
        
        if not success:
            return {"success": False, "status_code": 400, "detail": "已有該幣種持倉或系統異常"}
            
        engine.release_manual_close_state(canonical_symbol)
        if canonical_symbol in engine.account.positions:
            engine._take_over_manual_position(canonical_symbol, engine.account.positions[canonical_symbol])
        
        return {"success": True, "message": f"手動開倉 {side} {canonical_symbol} 成功"}
