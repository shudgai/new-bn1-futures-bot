"""Live entries and indicator refresh; held exits use the 5M+15M dual timeframe policy."""
import copy
import math
import time
import logging
import hashlib
import json
from core.services.strategies.strict_state_machine import StrictStateMachineStrategy, PositionState
from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate
from core.gates.trend_holding_immunity_and_exit_gate import TrendHoldingAndExitManager

logger = logging.getLogger("uvicorn.error")

# 全域單一策略實例
strict_strategy = StrictStateMachineStrategy()


def _kc2bar_candidates(frame, symbol, quote):
    from core.services.entry_contract import evaluate_entry_contract
    from core.services.wait_entry_authority import WaitEntryAuthority

    if (
        frame is None
        or frame.empty
        or not frame.attrs.get("entry_finality_verified")
        or frame.attrs.get("timeframe_ms") not in (60_000, 180_000)
    ):
        return []
    candidates = []
    for side in ("LONG", "SHORT"):
        code = f"KC_2BAR_CONFIRM_{side}"
        decision = evaluate_entry_contract(
            frame,
            price=quote,
            code=code,
            symbol=symbol,
        )
        if (
            not isinstance(decision, dict)
            or decision.get("action") != "ENTER"
            or decision.get("side") != side
            or decision.get("entry_phase") != "KC_2BAR_CLOSED_CONFIRM"
        ):
            continue
        breakout_bar_id = decision.get("breakout_bar_id")
        confirmation_bar_id = decision.get("pair_confirmation_bar_id")
        live_bar_id = decision.get("confirmation_bar_id")
        try:
            breakout_bar_id = int(float(breakout_bar_id))
            confirmation_bar_id = int(float(confirmation_bar_id))
            live_bar_id = int(float(live_bar_id))
        except (TypeError, ValueError, OverflowError):
            continue
        if (
            breakout_bar_id <= 0
            or confirmation_bar_id != breakout_bar_id + 60_000
            or live_bar_id != confirmation_bar_id + 60_000
        ):
            continue
        # Claims hash canonical integer candle IDs. Normalize the signal copy
        # too, otherwise an equivalent float ID (``123.0``) hashes differently
        # at the engine's final authorization check (``123``).
        decision = dict(decision)
        decision["breakout_bar_id"] = breakout_bar_id
        decision["pair_confirmation_bar_id"] = confirmation_bar_id
        decision["confirmation_bar_id"] = live_bar_id
        signal_id = str(decision.get("pending_signal_id") or "")
        if signal_id != f"{side}:{breakout_bar_id}:{confirmation_bar_id}":
            continue
        trigger_id = hashlib.sha256(
            (
                f"KC2BAR|{WaitEntryAuthority.canonical_symbol(symbol)}|{side}|{breakout_bar_id}|"
                f"{confirmation_bar_id}|{live_bar_id}"
            ).encode("utf-8"),
        ).hexdigest()
        candidates.append({
            "status": "TRIGGER_AVAILABLE",
            "authority": "KC2BAR",
            "trigger_id": trigger_id,
            "symbol": symbol,
            "side": side,
            "live_bar_id": live_bar_id,
            "setup_bar_id": breakout_bar_id,
            "signal": decision,
            "quote": float(quote),
            "atr": float(decision.get("entry_atr") or 0.0),
        })
    return candidates


def _filter_authorities_by_kc_direction(authorities, frame):
    """Remove entry authorities opposed to the latest verified closed KC slope."""
    from core.services.candle_data import closed_entry_candles

    if (
        frame is None
        or frame.empty
        or not frame.attrs.get("entry_finality_verified")
        or frame.attrs.get("timeframe_ms") not in (60_000, 180_000)
    ):
        return authorities, None
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 2 or "kc_middle" not in closed:
            return authorities, None
        previous = float(closed.iloc[-2]["kc_middle"])
        current = float(closed.iloc[-1]["kc_middle"])
        if not math.isfinite(previous) or not math.isfinite(current):
            return authorities, None
    except (KeyError, TypeError, ValueError, OverflowError):
        return authorities, None

    from core.gates.breakout_continuation_entry_gate import INSTANT_BREAKOUT_INSIDE
    immediate_breakouts = [
        item for item in authorities
        if item.get("authority") == "WILD_SWING"
        and item.get("signal", {}).get("structure_confirmation")
        in {INSTANT_BREAKOUT_INSIDE, "MEME_PEAK_HUNTER"}
    ]

    # Flat/unknown KC is not permission to choose a side. Keep the existing
    # conflict veto in that case; a directional closed KC can resolve only
    # the authority that points against it.
    if current > previous:
        allowed_side = "LONG"
    elif current < previous:
        allowed_side = "SHORT"
    else:
        return authorities, None
    filtered = [
        item for item in authorities
        if item.get("side") == allowed_side or item in immediate_breakouts
    ]
    return filtered, allowed_side


def _dispatch_wait_outbox(authority, symbol):
    events = authority.pending_events(symbol)
    if not events:
        return
    for event in events:
        logger.info(
            "WAIT_OUTBOX_EVENT %s",
            json.dumps(event, sort_keys=True, separators=(",", ":")),
        )
    authority.acknowledge_events([event["event_id"] for event in events])


def _observe_wait_entry(engine, symbol, frame, quote, position_open):
    from core.config import PAPER_TRADING, WATCHDOG_MAX_TIMEOUT_SECONDS
    from core.services.candle_data import closed_entry_candles

    authority = getattr(engine, "wait_entry_authority", None)
    if authority is None or frame is None or frame.empty:
        return None
    if frame.attrs.get("timeframe_ms") not in (60_000, 180_000):
        return None
    closed = closed_entry_candles(frame)
    quote_provider = getattr(engine, "_wait_quote_snapshot", None)
    if not callable(quote_provider):
        return None
    quote_snapshot = quote_provider(symbol)
    quote_fresh = False
    try:
        observed_at = float(quote_snapshot["observed_at"])
        quote = float(quote_snapshot["price"])
        quote_age = time.monotonic() - observed_at
        quote_fresh = (
            math.isfinite(quote)
            and quote > 0
            and math.isfinite(observed_at)
            and 0 <= quote_age <= WATCHDOG_MAX_TIMEOUT_SECONDS
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        quote_fresh = False
    if quote_fresh:
        quote = float(quote_snapshot["price"])
    live_row = frame.iloc[-1].to_dict()
    if quote_fresh and not bool(live_row.get("is_closed", False)):
        live_row["close"] = quote
        live_row["high"] = max(float(live_row["high"]), quote)
        live_row["low"] = min(float(live_row["low"]), quote)
    latest_closed_atr = (
        float(closed.iloc[-1]["atr"])
        if not closed.empty and "atr" in closed
        else 0.0
    )
    live_row["atr"] = latest_closed_atr
    live_row["is_closed"] = bool(live_row.get("is_closed", False))
    closed_rows = closed.tail(2).to_dict("records")
    for row in closed_rows:
        row["is_closed"] = True
    account = engine.account
    position_state_verified = isinstance(getattr(account, "positions", None), dict)
    if not PAPER_TRADING:
        position_state_verified = (
            position_state_verified
            and time.time() - float(getattr(account, "last_sync_at", 0.0)) <= 5.0
        )
    result = authority.observe(
        symbol,
        closed_rows,
        live_row,
        quote,
        quote_fresh=quote_fresh,
        position_open=position_open,
        authoritative_position_state=position_state_verified,
    )
    if result.get("status") in ("TRIGGER_AVAILABLE", "TRIGGER_CLAIMED"):
        logger.info(
            "WAIT_ENTRY_CANDIDATE symbol=%s side=%s trigger=%s status=%s",
            symbol, result.get("side"), result.get("trigger_id"),
            result.get("status"),
        )
        return result
    return None


async def _reconcile_wait_claims(engine, symbol):
    authority = getattr(engine, "wait_entry_authority", None)
    if authority is None:
        return
    claims = authority.unknown_claims(symbol)
    if not claims:
        return
    account = engine.account
    reconciler = getattr(account, "reconcile_wait_entry_claims", None)
    if callable(reconciler):
        await reconciler(authority, symbol)
        return
    for claim in claims:
        position = account.positions.get(symbol)
        position_claim_id = (
            position.get("entry_authority_claim_id")
            or position.get("wait_trigger_id")
            if position
            else None
        )
        position_identity_matches = bool(
            position
            and position_claim_id == claim["trigger_id"]
            and position.get("position_uuid") == claim.get("position_uuid")
        )
        if position is not None and not position_identity_matches:
            continue
        quantity = float(position.get("qty") or 0.0) if position else 0.0
        authority.reconcile(
            claim["trigger_id"],
            filled_qty=quantity,
            position_present=position_identity_matches,
            order_reconciled=True,
        )


async def _submit_wait_entry(engine, symbol, frame, candidate):
    authority = engine.wait_entry_authority
    if candidate.get("status") != "TRIGGER_AVAILABLE":
        return False
    trigger_id = str(candidate.get("trigger_id") or "")
    if not trigger_id or not authority.claim(trigger_id):
        return False
    client_order_id = "WAIT" + trigger_id[:32]
    if not authority.bind_order_identity(trigger_id, client_order_id):
        authority.resolve(
            trigger_id,
            filled_qty=0.0,
            position_present=False,
            outcome="FAILED",
        )
        return False
    return await _submit_claimed_authority(
        engine,
        symbol,
        frame,
        candidate,
        authority_kind="WAIT",
        client_order_id=client_order_id,
        metadata={
            "wait_trigger": dict(candidate),
            "wait_trigger_id": trigger_id,
            "wait_client_order_id": client_order_id,
        },
    )


async def _submit_kc2bar_entry(engine, symbol, frame, candidate):
    authority = engine.wait_entry_authority
    trigger_id = str(candidate.get("trigger_id") or "")
    client_order_id = "KC2BAR" + trigger_id[:30]
    if not trigger_id or not authority.claim_external(
        trigger_id,
        symbol=symbol,
        side=str(candidate["side"]),
        authority="KC2BAR",
        live_bar_id=int(candidate["live_bar_id"]),
        setup_bar_id=int(candidate["setup_bar_id"]),
        client_order_id=client_order_id,
    ):
        return False
    return await _submit_claimed_authority(
        engine,
        symbol,
        frame,
        candidate,
        authority_kind="KC2BAR",
        client_order_id=client_order_id,
        metadata={
            "kc2bar_signal": dict(candidate["signal"]),
            "kc2bar_signal_id": trigger_id,
        },
    )


async def _submit_claimed_authority(
    engine,
    symbol,
    frame,
    candidate,
    *,
    authority_kind,
    client_order_id,
    metadata,
):
    authority = engine.wait_entry_authority
    trigger_id = str(candidate["trigger_id"])
    entry_metadata = {
        **metadata,
        "entry_authority": authority_kind,
        "entry_authority_claim_id": trigger_id,
        "entry_client_order_id": client_order_id,
        "position_uuid": authority.position_uuid_for_trigger(trigger_id),
    }
    try:
        opened = await engine.execute_dual_tf_entry(
            symbol=symbol,
            frame=frame,
            price=float(candidate["quote"]),
            side=str(candidate["side"]),
            mode_name=(
                "WAIT_LIVE_BIG_TRIGGER"
                if authority_kind == "WAIT"
                else str(candidate["signal"]["type"])
            ),
            meta_dict=entry_metadata,
            entry_mode="CHANNEL_SWING",
        )
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "[FORBIDDEN_ENTRY]",
        ):
            authority.resolve(
                trigger_id,
                filled_qty=0.0,
                position_present=False,
                outcome="FAILED",
            )
            logger.warning(
                "%s_ENTRY_REJECTED_BEFORE_SUBMIT symbol=%s side=%s trigger=%s reason=%s",
                authority_kind,
                symbol,
                candidate.get("side"),
                trigger_id,
                exc,
            )
            return False
        position = engine.account.positions.get(symbol)
        quantity = float(position.get("qty") or 0.0) if position else 0.0
        authority.resolve(
            trigger_id,
            filled_qty=quantity,
            position_present=bool(position),
            outcome="UNKNOWN",
        )
        logger.exception(
            "%s_ENTRY_SUBMISSION_UNKNOWN symbol=%s side=%s trigger=%s",
            authority_kind, symbol, candidate.get("side"), trigger_id,
        )
        raise

    position = engine.account.positions.get(symbol)
    quantity = float(position.get("qty") or 0.0) if position else 0.0
    execution = getattr(engine.account, "wait_entry_execution", {}).get(symbol)
    terminal = isinstance(execution, dict) and execution.get("status") in {
        "closed", "canceled", "expired", "rejected",
    }
    remaining_reconciled = not hasattr(engine.account, "last_sync_at")
    execution_reconciled = remaining_reconciled or terminal
    filled_quantity = quantity
    if hasattr(engine.account, "last_sync_at"):
        if isinstance(execution, dict):
            filled_quantity = float(execution.get("filled_qty") or 0.0)
            remaining_reconciled = bool(
                terminal
                and execution.get("requested_qty") is not None
                and filled_quantity > 0
                and position is not None
                and position.get("entry_authority_claim_id") == trigger_id
                and position.get("position_uuid")
                == authority.position_uuid_for_trigger(trigger_id)
                and math.isclose(
                    quantity, filled_quantity, rel_tol=1e-8, abs_tol=1e-12,
                )
            )
    if opened and position and quantity > 0:
        authority.resolve(
            trigger_id,
            filled_qty=filled_quantity,
            position_present=True,
            outcome="FILLED",
            remaining_reconciled=remaining_reconciled,
        )
        return remaining_reconciled
    authority.resolve(
        trigger_id,
        filled_qty=filled_quantity,
        position_present=bool(position),
        outcome=(
            "FAILED"
            if (
                not opened
                and not position
                and execution_reconciled
            )
            else "UNKNOWN"
        ),
    )
    return False


async def _process_wild_swing_symbol(
    engine, symbol, frame, quote, daily_halt, exit_only,
):
    from core.services.candle_data import closed_entry_candles
    from core.services.exits.kc_true_extreme_exit import STATE_KEY as KC_EXIT_STATE_KEY
    from core.gates.breakout_continuation_entry_gate import (
        CONFIRMED_2BAR_BREAKOUT,
        CONTINUATION_OUTSIDE_KC,
        INSTANT_BREAKOUT_INSIDE,
    )
    from core.services.strategies.wild_swing_strategy import WildSwingStrategy

    strategy = getattr(engine, "_wild_swing_strategy", None)
    if strategy is None:
        strategy = engine._wild_swing_strategy = WildSwingStrategy()
    closed = closed_entry_candles(frame)
    if closed.empty or "timestamp" not in closed:
        return [], []

    latest_closed_bar_id = float(closed.iloc[-1]["timestamp"])
    if symbol not in strategy.states:
        strategy.reset_symbol(symbol, latest_closed_bar_id)

    if not hasattr(engine, "_channel_exit_frames"):
        engine._channel_exit_frames = {}
    engine._channel_exit_frames[symbol] = frame.copy()

    await _reconcile_wait_claims(engine, symbol)
    position = engine.account.positions.get(symbol)
    entry_frame = frame
    # CAP/Lobster entry decisions must pass the strategy's live quote and
    # structure checks. External WAIT and KC2BAR authorities can otherwise
    # authorize entries without the same width/MA5 and two-bar short filters.
    if position:
        wait_candidate = None
    else:
        boundary_provider = getattr(engine, "_entry_boundary_frame", None)
        if not callable(boundary_provider):
            logger.warning(
                "WAIT_ENTRY_BLOCK symbol=%s reason=FINALITY_PROVIDER_MISSING",
                symbol,
            )
            entry_frame = None
        else:
            try:
                entry_frame = await boundary_provider(symbol)
            except Exception:
                logger.exception(
                    "WAIT_ENTRY_BLOCK symbol=%s reason=FINALITY_REFRESH_FAILED",
                    symbol,
                )
                entry_frame = None
        if (
            entry_frame is None
            or entry_frame.empty
            or not entry_frame.attrs.get("entry_finality_verified")
            or entry_frame.attrs.get("timeframe_ms") not in (60_000, 180_000)
        ):
            logger.warning(
                "WAIT_ENTRY_BLOCK symbol=%s reason=UNVERIFIED_FINALITY_FRAME",
                symbol,
            )
            entry_frame = None
            wait_candidate = None
        else:
            wait_candidate = None
    _dispatch_wait_outbox(engine.wait_entry_authority, symbol)
    if position:
        meta_store = getattr(engine.account, "position_meta", None)
        if not isinstance(meta_store, dict):
            meta_store = {}
            engine.account.position_meta = meta_store
        position_meta = meta_store.setdefault(symbol, {})
        exit_decision = strategy.evaluate_exit(
            symbol,
            position,
            frame,
            quote=quote,
        )
        if exit_decision is None:
            from core.services.strategies.meme_peak_hunter import MemePeakHunterStrategy
            from core.symbol_aliases import is_meme_breakout_symbol
            if is_meme_breakout_symbol(symbol):
                meme_exit = MemePeakHunterStrategy.evaluate_exit(symbol, position, frame, quote)
                if meme_exit is not None:
                    exit_code, exit_reason, exit_details = meme_exit
                    exit_decision = {
                        "side": position.get("side"),
                        "reason": exit_code,
                        "bar_id": frame.iloc[-1].get("timestamp", 0.0) if len(frame) > 0 else 0.0,
                        "kc_exit": exit_details,
                        "entry_mode": position.get("entry_mode", "WILD_SWING"),
                    }
        position_state_changed = False
        for key in ("highest_price", "lowest_price"):
            value = position.get(key)
            if value is None:
                continue
            try:
                price_extreme = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(price_extreme) or price_extreme <= 0:
                continue
            previous_extreme = position_meta.get(key)
            if key == "highest_price":
                updated_extreme = max(float(previous_extreme or 0.0), price_extreme)
            else:
                updated_extreme = min(
                    float(previous_extreme or price_extreme),
                    price_extreme,
                )
            if previous_extreme != updated_extreme:
                position_meta[key] = updated_extreme
                position_state_changed = True
        kc_exit_state = position.get(KC_EXIT_STATE_KEY)
        if (
            isinstance(kc_exit_state, dict)
            and position_meta.get(KC_EXIT_STATE_KEY) != kc_exit_state
        ):
            position_meta[KC_EXIT_STATE_KEY] = copy.deepcopy(kc_exit_state)
            position_state_changed = True
        if position.get("profit_floor_armed") is True:
            if position_meta.get("profit_floor_armed") is not True:
                position_meta["profit_floor_armed"] = True
                position_state_changed = True
        if position_state_changed:
            save_state = getattr(engine.account, "save_state", None)
            if callable(save_state):
                save_state()
            else:
                logger.error(
                    "WILD_SWING_PROFIT_LOCK_PERSISTENCE_BLOCK "
                    "symbol=%s reason=ACCOUNT_SAVE_UNAVAILABLE",
                    symbol,
                )
        if exit_decision is not None:
            live_bar = frame.iloc[-1].to_dict() if frame is not None and not frame.empty else {}
            logger.warning(
                "POSITION_EXIT_DECISION symbol=%s side=%s reason=%s price=%s "
                "bar_id=%s cause=%s details=%s live_open=%s live_close=%s "
                "live_ma5=%s live_kc_middle=%s entry_mode=%s",
                symbol,
                exit_decision.get("side"),
                exit_decision.get("reason"),
                quote,
                exit_decision.get("bar_id"),
                (exit_decision.get("kc_exit") or {}).get("cause"),
                json.dumps(exit_decision.get("kc_exit") or {}, sort_keys=True),
                live_bar.get("open"),
                quote,
                live_bar.get("ma5"),
                live_bar.get("kc_middle"),
                exit_decision.get("entry_mode"),
            )
            closed_ok = await engine.account.close_position(
                symbol,
                quote,
                "Wild Swing " + exit_decision["reason"],
                is_manual=True,
                is_limit=False,
            )
            logger.warning(
                "POSITION_EXIT_RESULT symbol=%s side=%s reason=%s accepted=%s",
                symbol,
                exit_decision.get("side"),
                exit_decision.get("reason"),
                closed_ok,
            )
            if closed_ok and symbol not in engine.account.positions:
                exit_price = getattr(engine.account, "last_closed_price", {}).get(
                    symbol, quote,
                )
                exit_bar_id = exit_decision["bar_id"]
                if frame is not None and not frame.empty:
                    live_bar = frame.iloc[-1]
                    live_bar_id = live_bar.get("timestamp")
                    if (
                        live_bar_id is not None
                        and live_bar.get("is_closed") is not None
                        and not bool(live_bar.get("is_closed"))
                    ):
                        exit_bar_id = float(live_bar_id)
                strategy.record_exit(
                    symbol,
                    exit_decision["side"],
                    exit_price,
                    exit_decision["reason"],
                    exit_bar_id,
                )
            elif exit_decision.get("kc_exit") is not None:
                logger.warning(
                    "WILD_SWING_KC_EXIT_RETRY_PENDING symbol=%s reason=%s",
                    symbol, exit_decision["reason"],
                )
        return [], []

    if exit_only or daily_halt or not math.isfinite(quote) or quote <= 0:
        return [], []
    if (
        symbol in getattr(engine.account, "closing_lock", set())
        or symbol in getattr(engine.account, "pending_limit_orders", {})
    ):
        return [], []

    kc2bar_candidates = []
    signals = [
        strategy.evaluate_entry(
            symbol, frame, side, tick_price=quote,
        )
        for side in ("LONG", "SHORT")
    ]
    from core.services.key_level_dip_sniper import KeyLevelDipSniperService
    for side in ("LONG", "SHORT"):
        dip_sig = KeyLevelDipSniperService.evaluate_entry(
            symbol, frame, side, tick_price=quote,
        )
        if dip_sig is not None:
            signals.append(dip_sig)
    from core.services.strategies.meme_peak_hunter import MemePeakHunterStrategy
    for side in ("LONG", "SHORT"):
        meme_sig = MemePeakHunterStrategy.evaluate_entry(
            symbol, frame, side, tick_price=quote,
        )
        if meme_sig is not None:
            signals.append(meme_sig)
    signals = [signal for signal in signals if signal is not None]
    authorities = []
    if wait_candidate is not None:
        authorities.append({**wait_candidate, "authority": "WAIT"})
    authorities.extend(kc2bar_candidates)
    authorities.extend(
        {"side": signal["side"], "authority": "WILD_SWING", "signal": signal}
        for signal in signals
    )
    authorities, kc_authorized_side = _filter_authorities_by_kc_direction(
        authorities, entry_frame,
    )
    if kc_authorized_side is not None:
        logger.info(
            "ENTRY_AUTHORITY_KC_DIRECTION_FILTER symbol=%s side=%s remaining=%s",
            symbol, kc_authorized_side,
            sorted({item["side"] for item in authorities}),
        )
    wait_authority = getattr(engine, "wait_entry_authority", None)
    if wait_authority is None:
        eligible_signals = [
            item["signal"] for item in authorities
            if item.get("authority") == "WILD_SWING"
        ]
        if len(eligible_signals) != 1:
            return [], []
        arbitration = {
            "status": "ONE_ORDER",
            "winner": {
                "authority": "WILD_SWING",
                "side": eligible_signals[0]["side"],
                "signal": eligible_signals[0],
            },
        }
    else:
        arbitration = wait_authority.arbitrate(authorities)
    if arbitration["status"] == "BLOCKED_OPPOSITE_AUTHORITIES":
        logger.warning(
            "ENTRY_AUTHORITY_ARBITRATION_BLOCK symbol=%s sides=%s",
            symbol, sorted({item["side"] for item in authorities}),
        )
        return [], []
    if arbitration["status"] == "NO_AUTHORITY":
        return [], []

    winner = arbitration["winner"]
    if winner["authority"] == "WAIT":
        await _submit_wait_entry(engine, symbol, entry_frame, wait_candidate)
        _dispatch_wait_outbox(engine.wait_entry_authority, symbol)
        return [], []
    if winner["authority"] == "KC2BAR":
        await _submit_kc2bar_entry(engine, symbol, entry_frame, winner)
        _dispatch_wait_outbox(engine.wait_entry_authority, symbol)
        return [], []

    signal = winner["signal"]
    if signal.get("structure_confirmation") not in {
        CONFIRMED_2BAR_BREAKOUT,
        INSTANT_BREAKOUT_INSIDE,
        CONTINUATION_OUTSIDE_KC,
        "MA_CROSSOVER",
        "FIVE_LINE_CONTINUATION",
        "INSTANT_BURST_BREAKOUT",
        "MA5_MA15_1M_CROSS",
        "DIP_SNIPER",
        "MEME_PEAK_HUNTER",
    }:
        logger.error(
            "WILD_SWING_ENTRY_BLOCK symbol=%s side=%s mode=%s reason=ENTRY_GATE_REQUIRED",
            symbol, signal["side"], signal.get("structure_confirmation"),
        )
        return [], []
    signal_bar_id = float(signal["bar_id"])
    if signal.get("is_intrabar") is True:
        live_bar = frame.iloc[-1]
        if (
            bool(live_bar.get("is_closed", True))
            or float(live_bar.get("timestamp", -1)) != signal_bar_id
        ):
            logger.info(
                "WILD_SWING_ENTRY_BLOCK symbol=%s side=%s bar=%s reason=LIVE_CANDLE_IDENTITY_CHANGED",
                symbol, signal["side"], signal_bar_id,
            )
            return [], []
        current_bar = live_bar
    else:
        current_bar = closed.iloc[-1]
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    macro_fields = (
        "open", "high", "low", "close", "kc_upper", "kc_lower",
        "kc_middle", "ma15", "atr",
    )
    missing_macro_fields = [field for field in macro_fields if field not in current_bar]
    if missing_macro_fields:
        macro_allowed = False
        macro_reason = (
            "VETO_INVALID_MARKET_DATA: missing "
            + ",".join(missing_macro_fields)
        )
    else:
        try:
            macro_bar = {
                field: float(current_bar[field])
                for field in ("open", "high", "low", "close")
            }
            macro_indicators = {
                field: float(current_bar[field])
                for field in ("kc_upper", "kc_lower", "kc_middle", "ma15", "atr")
            }
            if len(closed) >= 1:
                prev_bar = closed.iloc[-2] if len(closed) >= 2 else closed.iloc[-1]
                macro_indicators["ma15_previous"] = float(prev_bar.get("ma15", current_bar["ma15"]))
                macro_indicators["kc_middle_previous"] = float(prev_bar.get("kc_middle", current_bar["kc_middle"]))
                macro_indicators["ma5_previous"] = float(prev_bar.get("ma5", current_bar.get("ma5", current_bar["close"])))
            for field in (
                "ma5_current", "ma5_previous",
                "kc_middle_current", "kc_middle_previous",
                "ma15_current", "ma15_previous",
            ):
                if field in signal:
                    macro_indicators[field] = float(signal[field])
        except (TypeError, ValueError, OverflowError):
            macro_allowed = False
            macro_reason = "VETO_INVALID_MARKET_DATA: non-numeric structure"
        else:
            values = tuple(macro_bar.values()) + tuple(macro_indicators.values())
            if (
                not all(math.isfinite(value) for value in values)
                or macro_indicators["atr"] <= 0
                or macro_indicators["kc_upper"] <= macro_indicators["kc_lower"]
            ):
                macro_allowed = False
                macro_reason = "VETO_INVALID_MARKET_DATA: invalid KC/ATR values"
            else:
                macro_allowed, macro_reason = MacroCandleStructureGate().can_open(
                    signal["side"],
                    macro_bar,
                    macro_indicators,
                    symbol=symbol,
                    entry_pattern=signal.get("entry_pattern"),
                    structure_confirmation=signal.get("structure_confirmation"),
                    structure_confirmed=signal.get("structure_confirmed") is True,
                    channel_expanding=signal.get("channel_expanding") is True,
                )
    if not macro_allowed:
        logger.info(
            "【純數學 Gate 拒絕】symbol=%s side=%s pattern=%s bar=%s signal=%s reason=%s",
            symbol, signal["side"], signal["entry_pattern"],
            signal_bar_id, signal["reason"], macro_reason,
        )
        return [], []
    logger.info(
        "【純數學 Gate 通過: %s】symbol=%s side=%s mode=%s bar=%s",
        signal["entry_pattern"], symbol, signal["side"],
        signal["structure_confirmation"], signal_bar_id,
    )

    logger.info(
        "WILD_SWING_FINAL_VISION_GATE_PENDING symbol=%s side=%s confirmation=%s bar=%s",
        symbol, signal["side"], signal["structure_confirmation"], signal_bar_id,
    )
    from core.gates.trend_riding_strategy import can_open_position
    gate_ok, gate_reason, _ = can_open_position(
        # Keep the live candle for an intrabar WILD_SWING candidate. The gate
        # re-derives the same pattern against the current quote before submit.
        df=frame,
        side=signal["side"],
        quote=quote,
        symbol=symbol,
        last_closed_side=getattr(engine.account, 'last_closed_side', {}).get(symbol, ''),
        last_closed_ts=getattr(engine.account, 'last_closed_at', {}).get(symbol, 0.0),
        entry_confirmation=signal.get("structure_confirmation"),
    )
    if not gate_ok:
        log_entry_gate(engine, symbol, signal['side'], 'GATE', gate_reason, float(frame.iloc[-1].get('timestamp', 0)))
        logger.warning(f"[{symbol}] CRITICAL_BLOCK: Gatekeeper rejected {signal['side']}: {gate_reason}")
        print(f"[{symbol}] [進場攔截 VETO] 防禦門禁一票否決: {gate_reason}", flush=True)
        return [], []

    opened = await engine.execute_dual_tf_entry(
        symbol=symbol,
        frame=frame,
        price=quote,
        side=signal["side"],
        mode_name=f"WILD_SWING_{signal['reason']}",
        meta_dict=signal,
        entry_mode="WILD_SWING",
    )
    logger.info(
        "【執行下單】symbol=%s side=%s pattern=%s bar=%s result=%s",
        symbol, signal["side"], signal["entry_pattern"],
        signal_bar_id, "FILLED" if opened else "REJECTED",
    )
    if opened and symbol in engine.account.positions:
        strategy.record_entry_fill(symbol, signal["bar_id"])
    return [], []


async def process_single_symbol_runner(engine, symbol, now_time, btc_1m_turn, daily_halt,
                                       exit_frame=None, exit_quote=None, exit_only=False):
    # 1. 狀態與交易所同步（防重啟失步）
    position = engine.account.positions.get(symbol)
    if position:
        side = position.get("side")
        entry_price = float(position.get("entry_price", 0.0))
        max_profit = float(position.get("max_pnl_usdt", 0.0))
        if side == "LONG":
            strict_strategy.set_state(symbol, PositionState.LONG, {"entry_price": entry_price, "max_pnl": max_profit})
        elif side == "SHORT":
            strict_strategy.set_state(symbol, PositionState.SHORT, {"entry_price": entry_price, "max_pnl": max_profit})
    else:
        strict_strategy.set_state(symbol, PositionState.IDLE)

    # 2. 獲取 5M 操作級別 K 棒
    frame = exit_frame
    if frame is None:
        from core.symbol_aliases import is_meme_breakout_symbol
        timeframe = "3m" if is_meme_breakout_symbol(symbol) else "5m"
        frame = await engine.fetch_klines(symbol, timeframe=timeframe, limit=150, keep_live=True)
        if frame is not None and not frame.empty:
            frame = engine.strategy.compute_indicators(frame.copy())

    if frame is None or frame.empty:
        return [], []

    from core.symbol_aliases import is_meme_breakout_symbol
    wild_swing_symbol = is_meme_breakout_symbol(symbol)
    if wild_swing_symbol:
        timeframe_ms = frame.attrs.get("timeframe_ms")
        if timeframe_ms is not None and float(timeframe_ms) not in (180_000.0, 60_000.0):
            frame = await engine.fetch_klines(
                symbol, timeframe="3m", limit=150, keep_live=True,
            )
            if frame is None or frame.empty:
                return [], []
            frame = engine.strategy.compute_indicators(frame.copy())
            if float(frame.attrs.get("timeframe_ms", 0) or 0) not in (180_000.0, 60_000.0):
                return [], []
        quote = float(
            exit_quote
            if exit_quote is not None
            else (getattr(engine, "tickers", {}).get(symbol) or frame.iloc[-1]["close"])
        )
        if not math.isfinite(quote) or quote <= 0:
            return [], []
        if daily_halt and symbol not in engine.account.positions:
            return [], []
        return await _process_wild_swing_symbol(
            engine, symbol, frame, quote, daily_halt, exit_only,
        )

    if not hasattr(engine, '_channel_exit_frames'):
        engine._channel_exit_frames = {}
    engine._channel_exit_frames[symbol] = frame.copy()

    # 3. 獲取 15M 大趨勢級別 K 棒
    frame_15m = None
    try:
        frame_15m = await engine.fetch_klines(symbol, timeframe='15m', limit=80, keep_live=True)
        if frame_15m is not None and not frame_15m.empty:
            frame_15m = engine.strategy.compute_indicators(frame_15m.copy())
    except Exception:
        pass

    c_5m = float(frame.iloc[-1]['close']) if frame is not None and not frame.empty else None
    len_5m = len(frame) if frame is not None else 0
    len_15m = len(frame_15m) if frame_15m is not None else 0

    quote = float(exit_quote if exit_quote is not None else
                  (getattr(engine, 'tickers', {}).get(symbol) or frame.iloc[-1]['close']))

    if not math.isfinite(quote) or quote <= 0:
        return [], []

    if daily_halt and not position:
        logger.error(f"[{symbol}] [RUNNER_PROBE] 5M_Close: {quote} | 15M_Trend: None | Action: REJECT | RejectReason: DAILY_HALT")
        print(f"[{symbol}] [RUNNER_PROBE] 5M_Close: {quote} | 15M_Trend: None | Action: REJECT | RejectReason: DAILY_HALT", flush=True)
        return [], []

    from core.indicators.dual_timeframe_provider import DualTimeframeProvider
    trend_15m_dict = DualTimeframeProvider.get_15m_trend(frame_15m) if frame_15m is not None else {}
    trend_15m = trend_15m_dict.get('15m_big_trend', 'UNKNOWN')

    if position:
        probe_msg = (
            f"[{symbol}] [RUNNER_PROBE] len_5M: {len_5m} | len_15M: {len_15m} | "
            f"5M_Close: {c_5m} | 15M_Trend: {trend_15m} | Pos: {position.get('side')} | "
            f"Action: HOLD | RejectReason: ALREADY_IN_POSITION"
        )
        logger.error(probe_msg)
        print(probe_msg, flush=True)

    # 4. 更新持倉 PnL 與峰值
    unrealized_pnl = 0.0
    if position:
        entry_price = float(position.get("entry_price", 0.0))
        amount = float(position.get("amount", 0.0))
        if position.get("side") == "LONG":
            unrealized_pnl = (quote - entry_price) * amount
        elif position.get("side") == "SHORT":
            unrealized_pnl = (entry_price - quote) * amount

        position["unrealized_pnl"] = unrealized_pnl
        if unrealized_pnl > position.get("max_pnl_usdt", 0.0):
            position["max_pnl_usdt"] = unrealized_pnl

    # ══════════════════════════════════════════════════════════════════════
    # 【情況 A：當前「無持倉」 (IDLE) - 5M+15M 跨週期共振開倉】
    # ══════════════════════════════════════════════════════════════════════
    if not position:
        if exit_only:
            return [], []

        # ──【平倉冷卻期與二次進場回抽追蹤】──
        curr_ts = time.time() * 1000.0
        last_closed_ts = 0.0
        last_closed_action = ""
        last_closed_price = 0.0

        last_closed_raw = getattr(engine.account, 'last_closed_at', {}).get(symbol)
        if last_closed_raw:
            last_closed_ts = float(last_closed_raw) * 1000.0 if float(last_closed_raw) < 1e11 else float(last_closed_raw)
        last_closed_side_raw = getattr(engine.account, 'last_closed_side', {}).get(symbol)
        if last_closed_side_raw:
            last_closed_action = f"CLOSE_{last_closed_side_raw}"
        last_closed_price_raw = getattr(engine.account, 'last_closed_price', {}).get(symbol)
        if last_closed_price_raw:
            last_closed_price = float(last_closed_price_raw)

        for tr in getattr(engine.account, 'trades', []):
            if tr.get('symbol') == symbol and str(tr.get('action', '')).startswith('CLOSE_'):
                tr_ts = float(tr.get('timestamp') or tr.get('id') or 0.0)
                if tr_ts > 0:
                    if tr_ts < 1e11:
                        tr_ts *= 1000.0
                    if tr_ts > last_closed_ts or last_closed_ts == 0.0:
                        last_closed_ts = tr_ts
                        last_closed_action = str(tr.get('action', ''))
                        last_closed_price = float(tr.get('price', 0.0))

        # ──【平倉冷卻期：強制等待 5 根 1M K 棒 (300 秒 = 300,000 ms)】──
        # 徹底避免在同一個底部/頂部連續被巴
        COOLDOWN_MS = 300_000.0  # 強制等待 5 根 1M K 棒 (300 秒)
        is_in_cooldown = (last_closed_ts > 0) and ((curr_ts - last_closed_ts) < COOLDOWN_MS)

        # 分別評估做多與做空潛力
        from core.services.entry_contract import evaluate_entry_contract
        from core.services.candle_data import log_entry_gate

        # 先以 5M+15M 跨週期共振閘門審查
        from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend
        from core.indicators.dual_timeframe_provider import DualTimeframeProvider
        kc_trend_5m = resolve_kc_trend(frame)
        trend_15m_dict = DualTimeframeProvider.get_15m_trend(frame_15m) if frame_15m is not None else {}
        trend_15m = trend_15m_dict.get('15m_big_trend', 'UNKNOWN')

        for candidate_side in ('LONG', 'SHORT'):
            can_enter, gate_reason, meta_dict = MemeSharedProtectionGate.evaluate_entry(
                df_5m=frame, df_15m=frame_15m, quote=quote, side=candidate_side, symbol=symbol,
                last_closed_action=last_closed_action,
                last_closed_price=last_closed_price,
                last_closed_ts=last_closed_ts,
                curr_ts=curr_ts,
            )
            action = 'ENTER' if can_enter else 'REJECT'
            probe_msg = (
                f"[{symbol}] [RUNNER_PROBE] len_5M: {len_5m} | len_15M: {len_15m} | "
                f"5M_Close: {c_5m} | 15M_Trend: {trend_15m} | Side: {candidate_side} | "
                f"Action: {action} | RejectReason: {gate_reason}"
            )
            logger.error(probe_msg)
            print(probe_msg, flush=True)
            if can_enter:
                mode_name = (meta_dict or {}).get('mode', 'MODE_DUAL_TF')
                # ──【平倉後冷卻期硬檢核 (Cooldown Hard Gate)】──
                # 使用者鐵律：「剛觸發平空/平多離場後，強制等待 5 根 1M K 棒 (300秒)，嚴禁同向連續開倉」！
                is_same_side_cooldown = (
                    (candidate_side == 'SHORT' and last_closed_action in ('CLOSE_SHORT', 'EXIT_SHORT')) or
                    (candidate_side == 'LONG' and last_closed_action in ('CLOSE_LONG', 'EXIT_LONG'))
                )
                if is_in_cooldown and is_same_side_cooldown:
                    remaining_s = (COOLDOWN_MS - (curr_ts - last_closed_ts)) / 1000.0
                    cooldown_msg = f"[{symbol}] [冷卻攔截 VETO] 平倉後強制冷卻 5 根 K 棒中，剩餘 {remaining_s:.1f} 秒，禁止開 {candidate_side} ({mode_name})"
                    logger.info(cooldown_msg)
                    print(cooldown_msg, flush=True)
                    continue

                # 最終下單入口二度方向與 MA5 硬鎖檢核
                m5_metrics = DualTimeframeProvider.get_5m_metrics(frame)
                ma5_s = m5_metrics.get('5m_ma5_slope', 0.0)
                ma5_v = m5_metrics.get('5m_ma5', 0.0)
                kc_mid = m5_metrics.get('5m_kc_middle', 0.0)
                # 均線間距 (>= 0.20 ATR) 與 MA5 斜率動能 (>= 0.05 / <= -0.05 ATR) 終極過濾 (Mode 3 斷頭鍘刀已放寬)
                if mode_name != 'MODE3_GUILLOTINE_CUT':
                    ma_ok, ma_reason = MemeSharedProtectionGate.validate_ma_condition(
                        frame, candidate_side, min_gap_atr=0.20, min_down_slope_atr=-0.05, min_up_slope_atr=0.05
                    )
                    if not ma_ok:
                        logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {ma_reason}")
                        continue

                # 平倉後回抽二次進場終極過濾 (Post-Exit Pullback Re-entry Filter)
                pb_ok, pb_reason = MemeSharedProtectionGate.validate_pullback_reentry(
                    df=frame,
                    side=candidate_side,
                    quote=quote,
                    last_closed_action=last_closed_action,
                    last_closed_price=last_closed_price,
                    last_closed_ts=last_closed_ts,
                    curr_ts=curr_ts,
                    atr=m5_metrics.get('5m_atr', 0.0),
                    ma5=ma5_v,
                )
                if not pb_ok:
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {pb_reason}")
                    continue

                # 終極「漲勢末端/跌勢末端」與「獲利空間」硬阻斷 (Final Exhaustion & Profit Room VETO)
                strong_bullish_trend = bool((meta_dict or {}).get('strong_bullish_trend', False))
                strong_bearish_trend = bool((meta_dict or {}).get('strong_bearish_trend', False))
                bullish_continuation = bool((meta_dict or {}).get('is_bullish_pullback_continuation', False))
                bearish_continuation = bool((meta_dict or {}).get('is_bearish_pullback_continuation', False))
                exh_ok, exh_reason = MemeSharedProtectionGate.validate_trend_exhaustion(
                    frame,
                    candidate_side,
                    quote,
                    strong_bullish_trend=strong_bullish_trend,
                    strong_bearish_trend=strong_bearish_trend,
                    bullish_continuation=bullish_continuation,
                    bearish_continuation=bearish_continuation,
                )
                if not exh_ok:
                    log_entry_gate(engine, symbol, candidate_side, 'GATE', exh_reason, float(frame.iloc[-1].get('timestamp', 0)))
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {exh_reason}")
                    print(f"[{symbol}] [進場攔截 VETO] {exh_reason}", flush=True)
                    continue

                room_ok, room_reason = MemeSharedProtectionGate.validate_profit_room(frame, candidate_side, quote)
                if not room_ok:
                    log_entry_gate(engine, symbol, candidate_side, 'GATE', room_reason, float(frame.iloc[-1].get('timestamp', 0)))
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {room_reason}")
                    print(f"[{symbol}] [進場攔截 VETO] {room_reason}", flush=True)
                    continue

                if candidate_side == 'LONG':
                    price_ok = (quote >= ma5_v and quote >= kc_mid) if mode_name != 'MODE3_GUILLOTINE_CUT' else (quote >= min(ma5_v, kc_mid))
                    if ma5_s <= 0 or not price_ok:
                        ma5_block_msg = f"MA5_SLOPE_NOT_POSITIVE_{ma5_s}" if ma5_s <= 0 else "PRICE_BELOW_MA5_OR_MID"
                        log_entry_gate(engine, symbol, candidate_side, 'GATE', ma5_block_msg, float(frame.iloc[-1].get('timestamp', 0)))
                        logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked LONG due to non-positive MA5 slope ({ma5_s}) or price < MA5/Middle (quote={quote}, ma5={ma5_v}, mid={kc_mid})")
                        continue
                if candidate_side == 'SHORT':
                    price_ok = (quote <= ma5_v and quote <= kc_mid) if mode_name != 'MODE3_GUILLOTINE_CUT' else (quote <= max(ma5_v, kc_mid))
                    if ma5_s >= 0 or not price_ok:
                        ma5_block_msg = f"MA5_SLOPE_NOT_NEGATIVE_{ma5_s}" if ma5_s >= 0 else "PRICE_ABOVE_MA5_OR_MID"
                        log_entry_gate(engine, symbol, candidate_side, 'GATE', ma5_block_msg, float(frame.iloc[-1].get('timestamp', 0)))
                        logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked SHORT due to non-negative MA5 slope ({ma5_s}) or price > MA5/Middle (quote={quote}, ma5={ma5_v}, mid={kc_mid})")
                        continue

                # 終極「高低位追單、負乖離過大與針尖力竭」雙重把關 (Final Anti-Chase & Pinbar VETO)
                from core.gates.high_position_chase_and_defense_filter import HighPositionChaseAndDefenseFilter
                final_chase_filter = HighPositionChaseAndDefenseFilter()
                fc_ok, fc_reason, _ = final_chase_filter.validate_entry(
                    side=candidate_side,
                    df=frame,
                    quote=quote,
                    last_closed_action=last_closed_action,
                    last_closed_ts=last_closed_ts,
                    last_closed_price=last_closed_price,
                    timeframe_ms=60_000.0,
                    strong_trend=(
                        strong_bullish_trend if candidate_side == 'LONG'
                        else strong_bearish_trend
                    ),
                    bypass_extension_check=(
                        bullish_continuation if candidate_side == 'LONG'
                        else bearish_continuation
                    ),
                )
                if not fc_ok:
                    log_entry_gate(engine, symbol, candidate_side, 'GATE', fc_reason, float(frame.iloc[-1].get('timestamp', 0)))
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Final check blocked {candidate_side} due to {fc_reason}")
                    print(f"[{symbol}] [進場攔截 VETO] {fc_reason}", flush=True)
                    continue

                # ──【全域開倉防禦門禁 (一票否決制 Guard Clauses)】──
                from core.services.strategies.defensive_trend_strategy import (
                    check_long_entry,
                    check_short_entry,
                )
                if candidate_side == 'LONG':
                    entry_ok, entry_reason, _ = check_long_entry(frame, symbol=symbol)
                else:
                    entry_ok, entry_reason, _ = check_short_entry(frame, symbol=symbol)

                if not entry_ok:
                    log_entry_gate(engine, symbol, candidate_side, 'GATE', entry_reason, float(frame.iloc[-1].get('timestamp', 0)))
                    logger.warning(f"[{symbol}] CRITICAL_BLOCK: Defensive Entry Gate rejected {candidate_side} due to {entry_reason}")
                    print(f"[{symbol}] [進場攔截 VETO] 防禦門禁一票否決: {entry_reason}", flush=True)
                    continue

                auth_log = (f"[5M_15M_DUAL_TF_AUTHORIZED] Symbol: {symbol} | "
                            f"Mode: {mode_name} | Side: {candidate_side} | Price: {quote}")
                logger.info(auth_log)
                print(auth_log, flush=True)

                # 實盤下單直通（Force Direct Order Execution）
                opened = await engine.execute_dual_tf_entry(
                    symbol=symbol,
                    frame=frame,
                    price=quote,
                    side=candidate_side,
                    mode_name=mode_name,
                    meta_dict=meta_dict,
                )
                if opened and hasattr(engine.account, 'last_closed_at'):
                    if isinstance(engine.account.last_closed_at, dict):
                        engine.account.last_closed_at.pop(symbol, None)
                if opened:
                    from core.gates.ratchet_net_profit_gate import get_ratchet_manager
                    get_ratchet_manager(symbol).reset()
                return [], []
            else:
                if gate_reason and "INSUFFICIENT" not in gate_reason:
                    logger.info(f"[{symbol}] 5M+15M Gate rejected {candidate_side}: {gate_reason}")


        return [], []

    # ══════════════════════════════════════════════════════════════════════
    # 【情況 B：當前「有持倉」 (IN_LONG / IN_SHORT) - 5M+15M 持倉護航與兩道出場】
    # ══════════════════════════════════════════════════════════════════════
    can_exit, exit_reason, exit_details = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=frame, df_15m=frame_15m, quote=quote, candidate_reason=None,
    )

    if can_exit:
        exit_log = f"[DUAL_TF_EXIT_TRIGGERED] Symbol: {symbol} | Reason: {exit_reason} | Price: {quote}"
        logger.info(exit_log)
        print(exit_log, flush=True)
        is_limit = bool((exit_details or {}).get('is_limit', False))
        limit_price = (exit_details or {}).get('limit_price', quote)
        from core.services.exits.authorized_position_exit import automated_position_exit_allowed
        if not automated_position_exit_allowed(
            symbol, position, engine.account.position_meta.get(symbol, {}),
            f"DualTF {exit_reason}", quote, is_manual=True,
        ):
            logger.warning(
                "POSITION_EXIT_BLOCK symbol=%s side=%s reason=%s authority=STRICT_SHORT_EXIT_GATE",
                symbol, position.get("side"), exit_reason,
            )
            return [], []
        # 即時重置持倉狀態機，原子清除部位與標記
        closed_ok = await engine.account.close_position(
            symbol,
            quote,
            f"DualTF {exit_reason}",
            is_manual=True,
            is_limit=is_limit,
            limit_price=limit_price,
        )
        if not closed_ok:
            logger.warning(
                "POSITION_EXIT_RESULT symbol=%s side=%s reason=%s accepted=false",
                symbol, position.get("side"), exit_reason,
            )
            return [], []
        engine.account.positions.pop(symbol, None)
        if hasattr(engine.account, 'position_meta'):
            engine.account.position_meta.pop(symbol, None)
        strict_strategy.set_state(symbol, PositionState.IDLE)
        if hasattr(engine.account, 'last_closed_at'):
            if isinstance(engine.account.last_closed_at, dict):
                engine.account.last_closed_at[symbol] = time.time()
        from core.gates.ratchet_net_profit_gate import get_ratchet_manager
        get_ratchet_manager(symbol).reset()
        return [], []

    return [], []
