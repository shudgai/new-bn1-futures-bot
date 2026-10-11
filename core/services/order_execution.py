"""Serialized close-then-open execution for verified strategy reversals."""

import asyncio


class OrderExecutionService:
    """Coordinate a top engulfing reversal without opening before close fill."""

    @staticmethod
    async def execute_top_engulfing_flip(engine, symbol, frame, quote, position,
                                         exit_reason, details):
        locks = getattr(engine, "_atomic_flip_locks", None)
        if locks is None:
            locks = engine._atomic_flip_locks = {}
        async with locks.setdefault(symbol, asyncio.Lock()):
            account = engine.account
            if (account.positions.get(symbol) is not position
                    or str(position.get("side", "")).upper() != "LONG"
                    or not isinstance(details, dict)
                    or details.get("authorized_action") != "ACTION_FLIP_LONG_TO_SHORT"):
                return False

            from core.gates.holding_protection_gate import HoldingProtectionExitGate
            allowed, authorized_reason, verified = HoldingProtectionExitGate.validate_exit(
                position, frame, quote, exit_reason, details,
            )
            if (not allowed or authorized_reason != "EXIT_LONG_ON_TOP_WATERFALL_DUMP"
                    or verified.get("authorized_action") != "ACTION_FLIP_LONG_TO_SHORT"):
                account.log(
                    f"ATOMIC_FLIP_ABORT symbol={symbol} stage=EXIT_VALIDATION reason={authorized_reason}",
                    "WARNING",
                )
                return False

            from core.services.entry_contract import (
                TOP_WATERFALL_FLIP_CODE, evaluate_entry_contract,
            )
            decision = evaluate_entry_contract(
                frame, quote, code=TOP_WATERFALL_FLIP_CODE,
                account=account, symbol=symbol,
            )
            if decision is None or decision.get("side") != "SHORT":
                account.log(
                    f"ATOMIC_FLIP_ABORT symbol={symbol} stage=ENTRY_PRECHECK reason=ENTRY_NOT_AUTHORIZED",
                    "WARNING",
                )
                return False

            close_reason = "Channel Swing EXIT_LONG_ON_TOP_WATERFALL_DUMP"
            daily_loss_limit = getattr(account, "daily_loss_limit_hit", None)
            daily_halt = False
            if callable(daily_loss_limit):
                try:
                    result = daily_loss_limit()
                    daily_halt = bool(result[0] if isinstance(result, (tuple, list)) else result)
                except Exception:
                    daily_halt = True
            account.log(
                f"ATOMIC_FLIP_BEGIN symbol={symbol} from=LONG to=SHORT quote={quote}",
                "WARNING",
            )
            closed = await account.close_position(
                symbol, quote, close_reason, is_manual=True,
            )
            if not closed or symbol in account.positions:
                account.log(
                    f"ATOMIC_FLIP_ABORT symbol={symbol} stage=CLOSE fill_confirmed={bool(closed)}",
                    "ERROR",
                )
                return False

            if daily_halt:
                account.log(
                    f"ATOMIC_FLIP_ABORT symbol={symbol} stage=DAILY_RISK close_filled=True short_opened=False",
                    "WARNING",
                )
                return False

            opened = await engine._execute_confirmed_channel_break(
                symbol, frame, quote, "SHORT", daily_halt=daily_halt,
                v8_reason=TOP_WATERFALL_FLIP_CODE,
                candidate_bar_id=decision.get("confirmation_bar_id"),
            )
            final_position = account.positions.get(symbol)
            success = bool(
                opened and final_position
                and str(final_position.get("side", "")).upper() == "SHORT"
            )
            account.log(
                f"ATOMIC_FLIP_{'FILLED' if success else 'PARTIAL'} "
                f"symbol={symbol} close_filled=True short_opened={success}",
                "SUCCESS" if success else "ERROR",
            )
            return success
