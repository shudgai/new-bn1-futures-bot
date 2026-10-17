"""Fail-closed shared entry verification immediately before opening exposure."""
import math
import time
import logging
import hashlib

from core.gates.breakout_continuation_entry_gate import (
    PURE_MATH_ENTRY_CONFIRMATIONS,
)
from core.gates.meme_coin_gate import MEME_ENTRY_CODES
from core.services.entry_contract import (
    ENTRY_CODES, ENTRY_EVIDENCE_KEYS, KC_PENDING_CODES,
    evaluate_entry_contract,
)
from core.services.wait_entry_authority import WaitEntryAuthority

logger = logging.getLogger("uvicorn.error")


class EntryFirewall:
    @classmethod
    def verify_can_open(cls, frame, side, code, symbol=None):
        """Only a supported, freshly validated contract authorizes automatic entry."""
        from core.symbol_aliases import is_auto_entry_symbol
        if not is_auto_entry_symbol(symbol):
            raise ValueError(
                "[FORBIDDEN_ENTRY] AUTOMATIC_ENTRY_SYMBOL_DISABLED: "
                + str(symbol)
            )
        require_final_trend_alignment(frame, side, symbol)
        if code not in ENTRY_CODES:
            raise ValueError('[FORBIDDEN_ENTRY] 已停用的自動入口訊號')
        decision = evaluate_entry_contract(frame, code=code, symbol=symbol)
        if decision is None or decision['side'] != side:
            raise ValueError('[FORBIDDEN_ENTRY] 最新行情不符合入口訊號')
        return decision


def validate_entry_frame(frame, side, code, symbol=None):
    return EntryFirewall.verify_can_open(frame, side, code, symbol=symbol)


def final_trend_alignment_problem(frame, side):
    """Fail closed unless completed KC and MA5/MA15 agree with the order side."""
    from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend
    from core.services.candle_data import closed_entry_candles

    normalized_side = str(side or "").upper()
    if normalized_side not in ("LONG", "SHORT"):
        return "BLOCKED_FINAL_ENTRY_SIDE_INVALID"
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return "BLOCKED_FINAL_TREND_DATA_INSUFFICIENT"
        current, previous = closed.iloc[-1], closed.iloc[-2]
        ma5 = float(current["ma5"])
        ma15 = float(current["ma15"])
        kc_middle = float(current["kc_middle"])
        previous_middle = float(previous["kc_middle"])
        values = (ma5, ma15, kc_middle, previous_middle)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return "BLOCKED_FINAL_TREND_DATA_INVALID"

        trend = resolve_kc_trend(closed)
        if normalized_side == "LONG":
            if trend != "UP":
                return "BLOCKED_LONG_KC_NOT_UP"
            if ma5 <= ma15:
                return "BLOCKED_LONG_MA5_NOT_ABOVE_MA15"
        else:
            if trend != "DOWN":
                return "BLOCKED_SHORT_KC_NOT_DOWN"
            if ma5 >= ma15:
                return "BLOCKED_SHORT_MA5_NOT_BELOW_MA15"
        return None
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
        return "BLOCKED_FINAL_TREND_DATA_INVALID"


def require_final_trend_alignment(frame, side, symbol):
    from core.symbol_aliases import is_auto_entry_symbol

    if not is_auto_entry_symbol(symbol):
        return
    problem = final_trend_alignment_problem(frame, side)
    if problem:
        raise ValueError("[FORBIDDEN_ENTRY] " + problem)


async def validate_account_entry(account, symbol, side, context):
    from core.symbol_aliases import is_auto_entry_symbol
    if not is_auto_entry_symbol(symbol):
        raise ValueError(
            "[FORBIDDEN_ENTRY] AUTOMATIC_ENTRY_SYMBOL_DISABLED: "
            + str(symbol)
        )
    from core.config import is_entry_disabled
    if is_entry_disabled(symbol):
        raise ValueError("[FORBIDDEN_ENTRY] ENTRY_DISABLED_SYMBOL: " + symbol)
    context = context if isinstance(context, dict) else {}

    def finite_bar_id(value):
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if math.isfinite(parsed) else None

    if context.get("entry_authority") == "KC2BAR":
        if (
            str(context.get("entry_mode") or "").upper() != "CHANNEL_SWING"
            or context.get("source") != "KC2BAR_1M"
            or side not in ("LONG", "SHORT")
        ):
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_AUTHORITY_CONTEXT_INVALID")
        signal = context.get("kc2bar_signal")
        claim_id = str(context.get("entry_authority_claim_id") or "")
        client_order_id = str(context.get("entry_client_order_id") or "")
        if (
            not isinstance(signal, dict)
            or signal.get("side") != side
            or signal.get("type") != f"KC_2BAR_CONFIRM_{side}"
            or signal.get("entry_phase") != "KC_2BAR_CLOSED_CONFIRM"
            or not claim_id
            or not client_order_id
            or context.get("kc2bar_signal_id") != claim_id
            or client_order_id != "KC2BAR" + claim_id[:30]
            or context.get("position_uuid")
            != WaitEntryAuthority.position_uuid_for_trigger(claim_id)
        ):
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_SIGNAL_IDENTITY_INVALID")
        expected_signal_id = hashlib.sha256(
            (
                f"KC2BAR|{WaitEntryAuthority.canonical_symbol(symbol)}|{side}|"
                f"{signal.get('breakout_bar_id')}|"
                f"{signal.get('pair_confirmation_bar_id')}|"
                f"{signal.get('confirmation_bar_id')}"
            ).encode("utf-8"),
        ).hexdigest()
        if expected_signal_id != claim_id:
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_SIGNAL_IDENTITY_INVALID")
        if (
            symbol in getattr(account, "positions", {})
            or symbol in getattr(account, "closing_lock", set())
            or symbol in getattr(account, "pending_limit_orders", {})
        ):
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_ACCOUNT_STATE_BLOCK")
        authority = getattr(account, "wait_entry_authority", None)
        provider = getattr(account, "entry_frame_provider", None)
        quote_provider = getattr(account, "wait_quote_provider", None)
        if (
            authority is None
            or not callable(provider)
            or not callable(quote_provider)
        ):
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_REVALIDATION_PROVIDER_MISSING")
        if hasattr(account, "last_sync_at"):
            try:
                await account.refresh(force=True)
            except Exception as exc:
                raise ValueError(
                    "[FORBIDDEN_ENTRY] KC2BAR_POSITION_RECONCILIATION_FAILED"
                ) from exc
            if (
                symbol in getattr(account, "positions", {})
                or symbol in getattr(account, "closing_lock", set())
                or symbol in getattr(account, "pending_limit_orders", {})
            ):
                raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_ACCOUNT_STATE_BLOCK")
        try:
            fresh_frame = await provider(symbol)
        except Exception as exc:
            raise ValueError(
                "[FORBIDDEN_ENTRY] KC2BAR_MARKET_DATA_REFRESH_FAILED"
            ) from exc
        if (
            fresh_frame is None
            or fresh_frame.empty
            or not fresh_frame.attrs.get("entry_finality_verified")
            or fresh_frame.attrs.get("timeframe_ms") != 60_000
        ):
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_UNVERIFIED_MARKET_DATA")
        require_final_trend_alignment(fresh_frame, side, symbol)
        try:
            quote_snapshot = quote_provider(symbol)
            quote = float(quote_snapshot["price"])
            observed_at = float(quote_snapshot["observed_at"])
            from core.config import WATCHDOG_MAX_TIMEOUT_SECONDS
            quote_fresh = (
                math.isfinite(quote)
                and quote > 0
                and math.isfinite(observed_at)
                and 0 <= time.monotonic() - observed_at
                <= WATCHDOG_MAX_TIMEOUT_SECONDS
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_QUOTE_INVALID") from exc
        if not quote_fresh:
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_QUOTE_STALE")

        code = f"KC_2BAR_CONFIRM_{side}"
        decision = evaluate_entry_contract(
            fresh_frame,
            price=quote,
            code=code,
            account=account,
            symbol=symbol,
        )
        try:
            breakout_bar_id = int(float(decision["breakout_bar_id"]))
            confirmation_bar_id = int(float(decision["pair_confirmation_bar_id"]))
            live_bar_id = int(float(decision["confirmation_bar_id"]))
            signal_id = hashlib.sha256(
                (
                    f"KC2BAR|{WaitEntryAuthority.canonical_symbol(symbol)}|{side}|{breakout_bar_id}|"
                    f"{confirmation_bar_id}|{live_bar_id}"
                ).encode("utf-8"),
            ).hexdigest()
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_SIGNAL_STALE_OR_INVALID")
        position_verified = isinstance(getattr(account, "positions", None), dict)
        if hasattr(account, "last_sync_at"):
            position_verified = (
                position_verified
                and time.time() - float(account.last_sync_at) <= 5.0
            )
        if (
            not isinstance(decision, dict)
            or decision.get("action") != "ENTER"
            or decision.get("side") != side
            or decision.get("type") != code
            or decision.get("entry_phase") != "KC_2BAR_CLOSED_CONFIRM"
            or signal_id != claim_id
            or breakout_bar_id != int(signal["breakout_bar_id"])
            or confirmation_bar_id != int(signal["pair_confirmation_bar_id"])
            or live_bar_id != int(signal["confirmation_bar_id"])
            or not authority.is_external_claim_valid(
                claim_id,
                symbol,
                side,
                live_bar_id,
                breakout_bar_id,
                client_order_id,
            )
            or authority.has_unknown_claims(except_trigger_id=claim_id)
            or not position_verified
        ):
            raise ValueError("[FORBIDDEN_ENTRY] KC2BAR_SIGNAL_STALE_OR_INVALID")
        logger.info(
            "KC2BAR_ENTRY_FIREWALL_PASS symbol=%s side=%s trigger=%s",
            symbol, side, claim_id,
        )
        return {
            **decision,
            "side": side,
            "entry_mode": "CHANNEL_SWING",
            "price": quote,
            "entry_atr": float(decision["entry_atr"]),
        }

    if context.get("entry_authority") == "WAIT":
        if (
            str(context.get("entry_mode") or "").upper() != "CHANNEL_SWING"
            or context.get("source") != "WAIT_1M"
            or side not in ("LONG", "SHORT")
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_AUTHORITY_CONTEXT_INVALID")
        trigger = context.get("wait_trigger")
        trigger_id = str(context.get("wait_trigger_id") or "")
        authority = getattr(account, "wait_entry_authority", None)
        if authority is None:
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_REVALIDATION_PROVIDER_MISSING")
        if (
            not isinstance(trigger, dict)
            or trigger.get("trigger_id") != trigger_id
            or trigger.get("symbol") != symbol
            or trigger.get("side") != side
            or not trigger_id
            or context.get("wait_client_order_id") != "WAIT" + trigger_id[:32]
            or context.get("entry_authority_claim_id") != trigger_id
            or context.get("entry_client_order_id")
            != context.get("wait_client_order_id")
            or trigger.get("position_uuid")
            != authority.position_uuid_for_trigger(trigger_id)
            or context.get("position_uuid")
            != authority.position_uuid_for_trigger(trigger_id)
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_TRIGGER_IDENTITY_INVALID")
        if (
            symbol in getattr(account, "positions", {})
            or symbol in getattr(account, "closing_lock", set())
            or symbol in getattr(account, "pending_limit_orders", {})
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_ACCOUNT_STATE_BLOCK")
        provider = getattr(account, "entry_frame_provider", None)
        quote_provider = getattr(account, "wait_quote_provider", None)
        if authority is None or not callable(provider) or not callable(quote_provider):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_REVALIDATION_PROVIDER_MISSING")
        if hasattr(account, "last_sync_at"):
            try:
                await account.refresh(force=True)
            except Exception as exc:
                raise ValueError(
                    "[FORBIDDEN_ENTRY] WAIT_POSITION_RECONCILIATION_FAILED"
                ) from exc
            if (
                symbol in getattr(account, "positions", {})
                or symbol in getattr(account, "closing_lock", set())
                or symbol in getattr(account, "pending_limit_orders", {})
            ):
                raise ValueError("[FORBIDDEN_ENTRY] WAIT_ACCOUNT_STATE_BLOCK")
        try:
            fresh_frame = await provider(symbol)
        except Exception as exc:
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_MARKET_DATA_REFRESH_FAILED") from exc
        if (
            fresh_frame is None
            or fresh_frame.empty
            or not fresh_frame.attrs.get("entry_finality_verified")
            or fresh_frame.attrs.get("timeframe_ms") != 60_000
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_UNVERIFIED_MARKET_DATA")
        from core.services.candle_data import closed_entry_candles

        closed = closed_entry_candles(fresh_frame)
        if closed.empty or len(closed) < 2 or "is_closed" not in fresh_frame:
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_CLOSED_CANDLES_INVALID")
        live_row = fresh_frame.iloc[-1]
        if bool(live_row.get("is_closed", True)):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_LIVE_CANDLE_MISSING")
        required = ("timestamp", "open", "high", "low", "close", "atr")
        if any(field not in closed or field not in fresh_frame for field in required):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_MARKET_FIELDS_MISSING")
        closed_rows = closed.tail(2).to_dict("records")
        for row in closed_rows:
            row["is_closed"] = True
        live_candle = live_row.to_dict()
        live_candle["is_closed"] = False
        live_candle["atr"] = float(closed.iloc[-1]["atr"])
        quote_snapshot = quote_provider(symbol)
        if not isinstance(quote_snapshot, dict):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_QUOTE_MISSING")
        try:
            quote = float(quote_snapshot["price"])
            observed_at = float(quote_snapshot["observed_at"])
            from core.config import WATCHDOG_MAX_TIMEOUT_SECONDS
            quote_fresh = (
                math.isfinite(quote)
                and quote > 0
                and math.isfinite(observed_at)
                and 0 <= time.monotonic() - observed_at
                <= WATCHDOG_MAX_TIMEOUT_SECONDS
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_QUOTE_INVALID") from exc
        if not quote_fresh:
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_QUOTE_STALE")
        position_verified = isinstance(getattr(account, "positions", None), dict)
        if hasattr(account, "last_sync_at"):
            position_verified = (
                position_verified
                and time.time() - float(account.last_sync_at) <= 5.0
            )
        if (
            authority.has_unknown_claims(except_trigger_id=trigger_id)
            or not authority.is_order_identity_bound(
                trigger_id,
                str(context.get("wait_client_order_id") or ""),
            )
            or not authority.revalidate_claim(
            trigger,
            closed_rows,
            live_candle,
            quote,
            quote_fresh=quote_fresh,
            position_open=symbol in getattr(account, "positions", {}),
            authoritative_position_state=position_verified,
            )
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_TRIGGER_STALE_OR_INVALID")
        latest_atr = finite_bar_id(closed.iloc[-1]["atr"])
        if latest_atr is None or latest_atr <= 0:
            raise ValueError("[FORBIDDEN_ENTRY] WAIT_ATR_INVALID")
        logger.info(
            "WAIT_ENTRY_FIREWALL_PASS symbol=%s side=%s trigger=%s",
            symbol, side, trigger_id,
        )
        return {
            "type": "WAIT_LIVE_BIG_TRIGGER",
            "side": side,
            "entry_mode": "CHANNEL_SWING",
            "entry_phase": "WAIT_LIVE_BIG_TRIGGER",
            "confirmation_bar_id": int(trigger["live_bar_id"]),
            "entry_atr": latest_atr,
            "price": quote,
        }

    if str(context.get("entry_mode") or "").upper() == "WILD_SWING":
        if context.get("source") != "WILD_SWING_1M":
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_SOURCE_INVALID")
        if side not in ("LONG", "SHORT"):
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_INVALID_SIDE")
        if (
            symbol in getattr(account, "positions", {})
            or symbol in getattr(account, "closing_lock", set())
            or symbol in getattr(account, "pending_limit_orders", {})
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_ACCOUNT_STATE_BLOCK")
        provider = getattr(account, "entry_frame_provider", None)
        if not callable(provider):
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_MISSING_FRESH_MARKET_DATA")
        fresh_frame = await provider(symbol)
        if (
            fresh_frame is None
            or fresh_frame.empty
            or not fresh_frame.attrs.get("entry_finality_verified")
            or fresh_frame.attrs.get("timeframe_ms") != 60_000
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_UNVERIFIED_CLOSED_CANDLES")
        require_final_trend_alignment(fresh_frame, side, symbol)

        from core.services.strategies.wild_swing_strategy import WildSwingStrategy
        live_row = fresh_frame.iloc[-1]
        tick_price = None
        if "is_closed" in fresh_frame and not bool(live_row["is_closed"]):
            try:
                tick_price = float(live_row["close"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    "[FORBIDDEN_ENTRY] WILD_SWING_LIVE_QUOTE_INVALID"
                ) from exc
            if not math.isfinite(tick_price) or tick_price <= 0:
                raise ValueError(
                    "[FORBIDDEN_ENTRY] WILD_SWING_LIVE_QUOTE_INVALID"
                )
        signal = WildSwingStrategy().evaluate_entry(
            symbol,
            fresh_frame,
            side,
            tick_price=tick_price,
            expected_wick_bar_id=context.get("wick_trigger_bar_id"),
        )
        if signal is None:
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_SIGNAL_STALE_OR_INVALID")
        if signal.get("structure_confirmation") not in PURE_MATH_ENTRY_CONFIRMATIONS:
            raise ValueError(
                "[FORBIDDEN_ENTRY] WILD_SWING_NON_MATHEMATICAL_ENTRY_DISABLED"
            )
        from core.gates.entry_pattern_permitted_gate import EntryPatternPermittedGate

        pattern_allowed, pattern_reason = EntryPatternPermittedGate.check_signal(signal)
        if not pattern_allowed:
            raise ValueError(
                "[FORBIDDEN_ENTRY] " + pattern_reason
            )
        signal_pattern = EntryPatternPermittedGate.normalize(
            signal.get("entry_pattern") or signal.get("entry_signal_mode")
        )
        if signal_pattern is None:
            raise ValueError(
                "[FORBIDDEN_ENTRY] REJECT_UNAUTHORIZED_ENTRY_PATTERN"
            )
        expected = context.get("dual_tf_meta")
        expected_bar = expected.get("bar_id") if isinstance(expected, dict) else None
        expected_pattern = EntryPatternPermittedGate.normalize(
            context.get("entry_pattern")
        )
        signal_bar_id = finite_bar_id(signal.get("bar_id"))
        expected_bar_id = finite_bar_id(expected_bar)
        confirmation_bar_id = finite_bar_id(
            context.get("channel_confirmation_bar_id")
        )
        expected_wick_bar_id = finite_bar_id(
            context.get("wick_trigger_bar_id")
        )
        signal_wick_bar_id = finite_bar_id(
            signal.get("wick_trigger_bar_id")
        )
        if (
            signal.get("side") != side
            or signal_pattern is None
            or signal_pattern != expected_pattern
            or (
                context.get("entry_signal_mode") is not None
                and EntryPatternPermittedGate.normalize(
                    context.get("entry_signal_mode")
                ) != signal_pattern
            )
            or context.get("entry_signal_code")
            != f"WILD_SWING_{signal.get('reason')}"
            or signal_bar_id is None
            or expected_bar_id != signal_bar_id
            or confirmation_bar_id != signal_bar_id
            or expected_wick_bar_id != signal_wick_bar_id
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_SIGNAL_STALE_OR_INVALID")

        from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
        from core.services.candle_data import closed_entry_candles

        closed = closed_entry_candles(fresh_frame)
        if closed.empty:
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_NO_CLOSED_MARKET_DATA")
        if signal.get("is_intrabar") is True:
            current = fresh_frame.iloc[-1]
            if (
                bool(current.get("is_closed", True))
                or finite_bar_id(current.get("timestamp")) != signal_bar_id
            ):
                raise ValueError(
                    "[FORBIDDEN_ENTRY] WILD_SWING_LIVE_CANDLE_IDENTITY_CHANGED"
                )
        else:
            current = closed.iloc[-1]
        required = (
            "open", "high", "low", "close", "kc_upper", "kc_lower",
            "kc_middle", "ma15", "atr",
        )
        if any(field not in current for field in required):
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_MACRO_DATA_MISSING")
        try:
            macro_bar = {
                field: float(current[field])
                for field in ("open", "high", "low", "close")
            }
            macro_indicators = {
                field: float(current[field])
                for field in ("kc_upper", "kc_lower", "kc_middle", "ma15", "atr")
            }
            for field in (
                "ma5_current", "ma5_previous",
                "kc_middle_current", "kc_middle_previous",
            ):
                if field in signal:
                    macro_indicators[field] = float(signal[field])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "[FORBIDDEN_ENTRY] WILD_SWING_MACRO_DATA_INVALID"
            ) from exc
        macro_values = tuple(macro_bar.values()) + tuple(macro_indicators.values())
        if (
            not all(math.isfinite(value) for value in macro_values)
            or macro_indicators["kc_upper"] <= macro_indicators["kc_lower"]
            or macro_indicators["atr"] <= 0
        ):
            raise ValueError("[FORBIDDEN_ENTRY] WILD_SWING_MACRO_DATA_INVALID")
        macro_allowed, macro_reason = MacroCandleStructureGate().can_open(
            side,
            macro_bar,
            macro_indicators,
            entry_pattern=signal_pattern,
            structure_confirmation=signal.get("structure_confirmation"),
            structure_confirmed=signal.get("structure_confirmed") is True,
            channel_expanding=signal.get("channel_expanding") is True,
        )
        if not macro_allowed:
            raise ValueError(
                "[FORBIDDEN_ENTRY] WILD_SWING_MACRO_GATE: " + macro_reason
            )
        logger.info(
            "【GATE 通過: %s】symbol=%s side=%s bar=%s stage=final_firewall",
            signal_pattern, symbol, side, signal_bar_id,
        )
        logger.info(
            "WILD_SWING_PURE_MATH_FIREWALL_PASS symbol=%s side=%s confirmation=%s bar=%s",
            symbol, side, signal["structure_confirmation"], signal_bar_id,
        )
        return {
            "type": context.get("entry_signal_code"),
            "side": side,
            "entry_mode": "WILD_SWING",
            "entry_pattern": signal_pattern,
            "entry_signal_mode": signal_pattern,
            "entry_phase": "WILD_SWING_1M",
            "confirmation_bar_id": signal_bar_id,
            "close_price": float(signal["close"]),
            "price": float(signal["close"]),
        }

    code = context.get('entry_signal_code')
    if code not in ENTRY_CODES:
        from core.gates.pipeline import pipeline
        regime_problem = pipeline.market_regime_problem(symbol)
        if regime_problem:
            raise ValueError("[FORBIDDEN_ENTRY] " + regime_problem)
        raise ValueError('[FORBIDDEN_ENTRY] 缺少合法入口白名單訊號，禁止送單')
        
    # Retain identity checks, but a forming candle must be revalidated.
    snapshot = context.get('entry_snapshot')
    if snapshot:
        # Check snapshot identity
        if snapshot.get('symbol') != symbol:
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong symbol')
        if snapshot.get('side') != side:
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong side')
        if snapshot.get('signal_code') != code:
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong signal_code')
        if snapshot.get('signal_id') != context.get('signal_id'):
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong signal_id')
        if snapshot.get('candidate_bar_id') != context.get('candidate_bar_id'):
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong candidate_bar_id')
        if snapshot.get('closed_bar') != context.get('channel_confirmation_bar_id'):
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong closed_bar_id')
            
        # Continue through fresh market validation; cached color is not permission.
        
    provider = getattr(account, 'entry_frame_provider', None)
    if not callable(provider):
        raise ValueError('[FORBIDDEN_ENTRY] 缺少可重驗的行情來源')
    try:
        frame = await provider(symbol)
    except Exception as exc:
        raise ValueError("[FORBIDDEN_ENTRY] 無法取得最新行情") from exc
        
    if frame is None or not frame.attrs.get('entry_finality_verified'):
        raise ValueError('[FORBIDDEN_ENTRY] 收線資料尚未通過獨立取樣確認')
    require_final_trend_alignment(frame, side, symbol)
    quote = float(frame.iloc[-1]['close'])
    from core.services.entry_contract import (
        evaluate_death_cross_fast_lane,
        evaluate_golden_cross_fast_lane,
    )
    verified_fast_cross = (
        evaluate_golden_cross_fast_lane(frame, quote, symbol)
        if code == 'KC_GOLDEN_CROSS_FAST_LONG' and side == 'LONG'
        else evaluate_death_cross_fast_lane(frame, quote, symbol)
        if code == 'KC_DEATH_CROSS_FAST_SHORT' and side == 'SHORT'
        else None
    )
    from core.gates.pipeline import (
        DEATH_CROSS_FAST_SHORT_CODE, GOLDEN_CROSS_FAST_LONG_CODE,
        MOMENTUM_BURST_CODES, entry_strategy_type,
        fast_cross_evidence, fast_cross_ma15_slope_problem,
        is_meme_breakout_symbol, ma5_ma15_cross_chop_problem,
        strict_kc_entry_problem,
    )
    if code in (GOLDEN_CROSS_FAST_LONG_CODE, DEATH_CROSS_FAST_SHORT_CODE):
        slope_problem = fast_cross_ma15_slope_problem(
            frame, quote, side, symbol=symbol,
        )
        if slope_problem:
            raise ValueError('[FORBIDDEN_ENTRY] ' + slope_problem)
        if fast_cross_evidence(frame, quote, side, symbol=symbol) is None:
            raise ValueError(
                '[FORBIDDEN_ENTRY] BLOCKED_FAST_CROSS_MA15_SLOPE_OR_EVIDENCE'
            )
    meme_golden_cross = (
        code == GOLDEN_CROSS_FAST_LONG_CODE and side == 'LONG'
        and is_meme_breakout_symbol(symbol)
    )
    cross_chop_problem = (
        None if meme_golden_cross or code in KC_PENDING_CODES
        or code in MEME_ENTRY_CODES
        or code in (
            'BEARISH_INSTANT_BREAKOUT', 'BULLISH_INSTANT_BREAKOUT',
            'KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT',
            'KC_LIVE_PATTERN_BREAKOUT_LONG', 'KC_LIVE_PATTERN_BREAKOUT_SHORT',
            'KC_REALTIME_RAIL_BREACH_SHORT',
        ) else
        ma5_ma15_cross_chop_problem(
            frame, quote, symbol=symbol, log_result=True,
            side=side,
        )
    )
    if cross_chop_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + cross_chop_problem)
    breakout_codes = KC_PENDING_CODES | MEME_ENTRY_CODES | {
        'TRIGGER_A_KC_BREAKOUT', 'TRIGGER_C_CONTINUATION',
        'RE_ENTRY_LONG', 'RE_ENTRY_SHORT',
        'KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT',
        'KC_LIVE_PATTERN_BREAKOUT_LONG', 'KC_LIVE_PATTERN_BREAKOUT_SHORT',
        'KC_REALTIME_RAIL_BREACH_SHORT',
    }
    kc_problem = strict_kc_entry_problem(
        frame, quote, side,
        strategy_type=(
            'BREAKOUT' if code in breakout_codes
            else entry_strategy_type(context.get('entry_phase'))
        ),
    )
    if kc_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + kc_problem)
    from core.services.entry_contract import (
        opposite_side_close_cooldown_problem, same_bar_close_entry_problem,
        same_side_anti_churn_problem,
    )
    churn_problem = same_side_anti_churn_problem(
        account, symbol, frame, side, quote,
    )
    if churn_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + churn_problem)
    same_bar_problem = same_bar_close_entry_problem(
        account, symbol, frame, side,
    )
    if same_bar_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + same_bar_problem)
    opposite_cooldown_problem = (
        None if code in ('BEARISH_INSTANT_BREAKOUT', 'BULLISH_INSTANT_BREAKOUT')
        else opposite_side_close_cooldown_problem(
            account, symbol, frame, side,
        )
    )
    if opposite_cooldown_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + opposite_cooldown_problem)
    from core.gates.pipeline import long_large_bearish_candle_problem
    if side == 'LONG':
        bearish_candle_problem = long_large_bearish_candle_problem(frame, quote)
        if bearish_candle_problem:
            raise ValueError('[FORBIDDEN_ENTRY] ' + bearish_candle_problem)
    if (side == 'SHORT'
            and code in (
                'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                'TOP_WATERFALL_FLIP',
                'AUTHORIZED_TOP_REVERSAL_SHORT',
                'CLIMAX_REVERSAL_FLIP',
            )
            and str(getattr(account, 'last_closed_side', {}).get(symbol, '')).upper() == 'LONG'):
        from core.services.strategies.outer_strategy import kc_bearish_midline_turn
        if not kc_bearish_midline_turn(frame, quote):
            raise ValueError(
                '[FORBIDDEN_ENTRY] BLOCKED_SHORT_FLIP_WITHOUT_BEARISH_KC_TURN'
            )
    from core.gates.pipeline import FLAT_LINE_BREAKOUT_TYPES, PIPELINE_ENTRY_TYPES, pipeline
    is_outer_rail_breakout = (
        code in FLAT_LINE_BREAKOUT_TYPES
        or pipeline.detect_realtime_breakout(frame, quote, side=side) is not None
    )
    midline_detector = (
        pipeline.detect_momentum_burst
        if code in MOMENTUM_BURST_CODES
        else pipeline.detect_midline_break_long if side == 'LONG'
        else pipeline.detect_midline_break_short
    )
    verified_midline_entry = midline_detector(
        frame, quote, side=side,
    )
    verified_midline_entry = bool(
        verified_midline_entry
        and verified_midline_entry.get('type') == code
        and verified_midline_entry.get('side') == side
    )
    is_flip_or_shadow = (
        code in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP',
                 'CLIMAX_REVERSAL_FLIP', 'AUTHORIZED_SHADOW_RETEST_ENTRY',
                 'SHADOW_RETEST_ENTRY',
                 'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                 'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                 'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                 'AUTHORIZED_TOP_REVERSAL_SHORT')
        or code in (
            'BEARISH_INSTANT_BREAKOUT', 'BULLISH_INSTANT_BREAKOUT',
            'KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT',
            'KC_REALTIME_RAIL_BREACH_SHORT',
        )
    )
    is_verified_live_breakout = (
        pipeline.has_strong_bullish_breakout(frame, quote) if side == 'LONG'
        else pipeline.has_strong_bearish_breakout(frame, quote)
    )
    is_exempt_from_regime = bool(
        code in KC_PENDING_CODES or is_flip_or_shadow
        or is_verified_live_breakout or verified_midline_entry
        or verified_fast_cross is not None
        or code in MEME_ENTRY_CODES
    )
    regime_problem = pipeline.market_regime_problem(symbol, frame, quote)
    if regime_problem and not is_exempt_from_regime:
        raise ValueError("[FORBIDDEN_ENTRY] " + regime_problem)
    if (code not in KC_PENDING_CODES and not is_flip_or_shadow and not verified_midline_entry
            and verified_fast_cross is None and code not in MEME_ENTRY_CODES):
        try:
            chop_problem = pipeline.chop_lockout_problem(
                frame, quote, allow_flat_lines=is_outer_rail_breakout,
                signal_code=code,
                allow_ma_tangle=verified_fast_cross is not None,
            )
        except TypeError:
            chop_problem = pipeline.chop_lockout_problem(frame, quote)
        if chop_problem:
            raise ValueError('[FORBIDDEN_ENTRY] ' + chop_problem)
    grace = context.get('pipeline_ttl_grace')
    # The two remaining entry authorities are always recomputed from the fresh
    # frame. A cached pipeline TTL decision must never bypass their contract.
    if grace is not None and code not in ENTRY_CODES:
        from core.gates.pipeline import PIPELINE_ENTRY_TYPES
        try:
            elapsed = time.monotonic() - float(grace['authorized_at_monotonic'])
            authorized_price = float(grace['authorized_price'])
            atr = float(grace['entry_atr'])
            quote_timestamp = float(grace['quote_timestamp'])
            quote = float(frame.iloc[-1]['close'])
            current_bar = float(frame.iloc[-1]['timestamp'])
            authorized_decision = grace['decision']
            stamp = float(authorized_decision['confirmation_bar_id'])
            adverse_move = (
                max(0.0, authorized_price - quote)
                if side == 'LONG'
                else max(0.0, quote - authorized_price)
            )
            quote_age = time.time() - quote_timestamp
            if (
                elapsed > 3
                and code in PIPELINE_ENTRY_TYPES
                and isinstance(authorized_decision, dict)
                and authorized_decision.get('_is_authorized')
                and authorized_decision.get('type') == code
                and authorized_decision.get('side') == side
                and authorized_decision.get('pending_signal_id') == grace.get('pending_signal_id')
                and grace.get('pending_signal_id') == context.get('signal_id')
                and current_bar == stamp
                and all(math.isfinite(value) for value in (
                    authorized_price, atr, quote, current_bar, stamp,
                    quote_age, adverse_move,
                ))
                and authorized_price > 0
                and atr > 0
                and quote > 0
                and 0 <= quote_age <= 30
                and adverse_move / atr <= 0.8
            ):
                from core.gates.pipeline import pipeline
                refreshed_at = time.monotonic()
                refreshed = pipeline.authorize(
                    authorized_decision, frame, quote, symbol=symbol,
                    requested_side=side,
                )
                if (
                    refreshed is not None
                    and refreshed.get('_is_authorized')
                    and refreshed.get('type') == code
                    and refreshed.get('side') == side
                    and refreshed.get('pending_signal_id') == grace.get('pending_signal_id')
                    and refreshed.get('confirmation_bar_id') == stamp
                ):
                    authorized_decision = refreshed
                    authorized_price = quote
                    atr = float(refreshed['entry_atr'])
                    stamp = float(refreshed['confirmation_bar_id'])
                    elapsed = time.monotonic() - refreshed_at
                    adverse_move = 0.0
                    import logging
                    logging.getLogger('uvicorn.error').info(
                        '[ENTRY_FIREWALL] Refreshed same-bar pipeline authorization '
                        f'for {symbol} {side}: {code}'
                    )
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            raise ValueError('[FORBIDDEN_ENTRY] TTL grace evidence is invalid')
        failures = []
        if code not in PIPELINE_ENTRY_TYPES:
            failures.append('UNSUPPORTED_ENTRY_TYPE')
        if (
            not isinstance(authorized_decision, dict)
            or not authorized_decision.get('_is_authorized')
            or authorized_decision.get('type') != code
            or authorized_decision.get('side') != side
        ):
            failures.append('AUTHORIZATION_MISMATCH')
        if (
            not isinstance(authorized_decision, dict)
            or authorized_decision.get('pending_signal_id') != grace.get('pending_signal_id')
            or grace.get('pending_signal_id') != context.get('signal_id')
        ):
            failures.append('SIGNAL_ID_MISMATCH')
        values = (
            elapsed, authorized_price, atr, quote_timestamp, quote,
            current_bar, stamp, quote_age, adverse_move,
        )
        if not all(math.isfinite(value) for value in values):
            failures.append('NONFINITE_EVIDENCE')
        if not math.isfinite(elapsed) or not 0 <= elapsed <= 3:
            failures.append('AUTHORIZATION_TTL_EXPIRED')
        if not math.isfinite(authorized_price) or authorized_price <= 0:
            failures.append('INVALID_AUTHORIZED_PRICE')
        if not math.isfinite(atr) or atr <= 0:
            failures.append('INVALID_ATR')
        if not math.isfinite(quote) or quote <= 0:
            failures.append('INVALID_QUOTE')
        if not math.isfinite(current_bar) or current_bar <= 0:
            failures.append('INVALID_CURRENT_BAR')
        if not math.isfinite(quote_age) or not 0 <= quote_age <= 30:
            failures.append('STALE_QUOTE')
        adverse_atr = adverse_move / atr if math.isfinite(atr) and atr > 0 else math.inf
        if not math.isfinite(adverse_atr) or adverse_atr > 0.8:
            failures.append('ADVERSE_SLIPPAGE_LIMIT')
        if failures:
            metrics = (
                f'elapsed_seconds={elapsed:.3f} quote_age_seconds={quote_age:.3f} '
                f'adverse_slippage_atr={adverse_atr:.3f} current_bar={current_bar} '
                f'authorized_bar={stamp}'
            )
            raise ValueError(
                '[FORBIDDEN_ENTRY] TTL grace rejected: '
                + ','.join(dict.fromkeys(failures))
                + f'; {metrics}'
            )
        decision = dict(authorized_decision)
        decision['price'] = quote
        decision['close_price'] = quote
    else:
        diagnostics = {}
        decision = evaluate_entry_contract(frame, code=code, account=account, symbol=symbol, diagnostics=diagnostics)
        if decision is None or decision['side'] != side:
            raise ValueError('[FORBIDDEN_ENTRY] 冷卻或最新入口行情不符: ' + diagnostics['reason'])
    from core.gates.pipeline import entry_market_structure_problem
    market_structure_problem = entry_market_structure_problem(
        frame, float(frame.iloc[-1]['close']), side,
        entry_phase=decision.get('entry_phase'),
    )
    if market_structure_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + market_structure_problem)
    expected_id = snapshot.get('pending_signal_id') if snapshot else None
    if expected_id is not None and expected_id != decision.get('pending_signal_id'):
        raise ValueError('[FORBIDDEN_ENTRY] 原始突破訊號已改變')
    stamp = float(decision['confirmation_bar_id'])
    if grace is None:
        age = time.time() * 1000 - (stamp if decision.get('intrabar') else stamp + 60000)
        if not math.isfinite(age) or not 0 <= age <= (60000 if decision.get('intrabar') else 90000):
            raise ValueError('[FORBIDDEN_ENTRY] 已收線訊號過期或來自未來')
    else:
        decision['intrabar'] = True
    if context.get('channel_confirmation_bar_id') != stamp:
        raise ValueError('[FORBIDDEN_ENTRY] 下單確認 K 已改變')
    from core.services.candle_data import entry_frame_evidence
    if snapshot:
        snapshot.update(signal_code=decision['type'], closed_bar=stamp,
                        quote_price=decision['price'],
                        closed_price=decision['close_price'],
                        entry_phase=decision['entry_phase'],
                        breakout_bar_id=decision['breakout_bar_id'],
                        pair_confirmation_bar_id=decision['pair_confirmation_bar_id'],
                        finality_server_ms=frame.attrs.get('entry_finality_server_ms'),
                        evidence=entry_frame_evidence(frame))
        snapshot.update({key: decision[key] for key in ENTRY_EVIDENCE_KEYS if key in decision})
    return decision
