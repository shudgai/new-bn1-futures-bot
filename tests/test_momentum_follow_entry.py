"""
Unit tests for Position Flip (CLOSE_SHORT_AND_OPEN_LONG) and Momentum Follow Entry
under Bullish Macro Trend.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import pandas as pd
import numpy as np

from core.services.execution_manager import ExecutionManager

def test_momentum_follow_entry_under_bullish_macro():
    """
    Test that when in a Bullish Macro with NO position (FLAT),
    if MA3 > MA15 and Price > MA15 on a green candle,
    ExecutionManager triggers MOMENTUM_FOLLOW_LONG instead of staying FLAT.
    """
    async def _run():
        symbol = "TEST_BULL_USDT"
        
        # Create DataFrame with bullish structure (50 rows)
        n = 50
        closes = [100.0 + i * 0.2 for i in range(n)]
        opens = [c - 0.1 for c in closes]
        highs = [c + 0.3 for c in closes]
        lows = [o - 0.2 for o in opens]
        df = pd.DataFrame({
            "timestamp": [1700000000000 + i * 60000 for i in range(n)],
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000] * n,
            "kc_middle": [95.0 + i * 0.2 for i in range(n)],
            "kc_upper": [105.0 + i * 0.2 for i in range(n)],
            "kc_lower": [85.0 + i * 0.2 for i in range(n)],
            "ma3": [c - 0.05 for c in closes],
            "ma5": [c - 0.10 for c in closes],
            "ma15": [c - 0.30 for c in closes],
            "atr": [1.0] * n,
            "is_closed": [True] * n
        })

        # Mock broker / account
        account = MagicMock()
        account.account = account
        account.positions = {}
        account.paper_account = account

        with patch("core.services.execution_manager.ExecutionManager.open_position", new_callable=AsyncMock) as mock_open:
            mock_open.return_value = True
            
            result = await ExecutionManager.process_symbol_pipeline(
                symbol=symbol,
                engine_or_account=account,
                klines_df=df,
                quote=closes[-1]
            )

            assert result is not None
            assert result.get("status") == "ENTRY_EXECUTED"
            assert result.get("side") == "LONG"
            assert result.get("entry_type") in ("MOMENTUM_FOLLOW_LONG", "EARLY_BULL_BREAKOUT_LONG")
            assert mock_open.called
            call_args = mock_open.call_args[1]
            assert call_args["side"] == "LONG"
            assert call_args["meta"]["entry_type"] in ("MOMENTUM_FOLLOW_LONG", "EARLY_BULL_BREAKOUT_LONG")

    asyncio.run(_run())


def test_flip_short_to_long_under_bullish_macro():
    """
    Test that when holding a SHORT in a Bullish Macro,
    if exit trigger is hit on a reversal,
    ExecutionManager executes CLOSE_SHORT and directly FLIPS to LONG (PIVOT_FLIP_LONG).
    """
    async def _run():
        symbol = "TEST_FLIP_USDT"
        
        # Bullish DataFrame where price bounced off bottom and crosses above MA15 & KC_mid
        n = 50
        closes = [100.0 + i * 0.2 for i in range(n)]
        opens = [c - 0.1 for c in closes]
        highs = [c + 0.3 for c in closes]
        lows = [o - 0.2 for o in opens]
        df = pd.DataFrame({
            "timestamp": [1700000000000 + i * 60000 for i in range(n)],
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000] * n,
            "kc_middle": [95.0 + i * 0.2 for i in range(n)],
            "kc_upper": [105.0 + i * 0.2 for i in range(n)],
            "kc_lower": [85.0 + i * 0.2 for i in range(n)],
            "ma3": [c - 0.05 for c in closes],
            "ma5": [c - 0.10 for c in closes],
            "ma15": [c - 0.30 for c in closes],
            "atr": [1.0] * n,
            "is_closed": [True] * n
        })

        account = MagicMock()
        account.account = account
        # Position with SHORT
        short_pos = {
            "symbol": symbol,
            "side": "SHORT",
            "amount": 10.0,
            "qty": 10.0,
            "entry_price": 108.0,
            "highest_price": 108.0,
            "lowest_price": 95.0,
            "trough_price": 95.0,
            "peak_roe_pct": 12.0,
            "open_timestamp": 1700000000,
        }
        account.positions = {symbol: short_pos}
        account.paper_account = account

        with patch("core.services.execution_manager.ExecutionManager.close_position", new_callable=AsyncMock) as mock_close, \
             patch("core.services.execution_manager.ExecutionManager.open_position", new_callable=AsyncMock) as mock_open:
            mock_close.return_value = True
            mock_open.return_value = True
            
            result = await ExecutionManager.process_symbol_pipeline(
                symbol=symbol,
                engine_or_account=account,
                klines_df=df,
                quote=closes[-1]
            )

            assert result is not None
            assert mock_close.called
            # Verify flip happened
            assert mock_open.called
            call_args = mock_open.call_args[1]
            assert call_args["side"] == "LONG"
            assert "FLIP" in call_args["meta"]["entry_type"]

    asyncio.run(_run())
