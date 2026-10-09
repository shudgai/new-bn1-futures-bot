import math
from collections import defaultdict

class ReplayParityError(Exception):
    pass

class ReplayAuditGuard:
    """
    Mandatory fail-fast assertions for any trade-level replay or PnL simulation.
    Ensures replay parity matches production exactly.
    """
    def __init__(self):
        self.used_opens = set()
        self.used_closes = set()
        
    def check_pairing(self, open_trade, close_trade):
        """
        Rule 1: duplicate OPEN reuse = 0
        Rule 2: duplicate CLOSE reuse = 0
        Rule 5: symbol mapping parity
        """
        open_id = open_trade.get('id')
        close_id = close_trade.get('id')
        
        if open_id in self.used_opens:
            raise ReplayParityError(f"REPLAY_PARITY_FAIL: Duplicate OPEN reuse detected for ID {open_id}")
        if close_id in self.used_closes:
            raise ReplayParityError(f"REPLAY_PARITY_FAIL: Duplicate CLOSE reuse detected for ID {close_id}")
            
        if open_trade.get('symbol') != close_trade.get('symbol'):
            raise ReplayParityError(f"REPLAY_PARITY_FAIL: Symbol mismatch {open_trade.get('symbol')} != {close_trade.get('symbol')}")
            
        self.used_opens.add(open_id)
        self.used_closes.add(close_id)
        
    def check_timestamp_parity(self, position):
        """
        Rule 3: open_timestamp schema parity
        PRODUCTION CONTRACT: open_timestamp MUST be in SECONDS.
        If it's in milliseconds, ident[1]*1000 will be in microseconds and bypass all exits.
        """
        open_ts = position.get('open_timestamp')
        if not open_ts or not math.isfinite(open_ts):
            raise ReplayParityError("REPLAY_PARITY_FAIL: Missing or invalid open_timestamp")
            
        # Strict schema validation: Valid Unix timestamps in seconds for modern times 
        # (e.g., 2024-2030) are roughly between 1.7B and 2.0B.
        # Values like 1.7T (milliseconds) strictly violate the Production Contract.
        if not (1000000000 < open_ts < 3000000000):
            raise ReplayParityError(
                f"REPLAY_PARITY_FAIL: SCHEMA CONTRACT VIOLATION. "
                f"open_timestamp {open_ts} violates SECONDS schema constraint. "
                f"Ensure timestamp is converted to seconds before creating the replay position."
            )
            
    def verify_known_exit(self, replay_exit_reason, replay_exit_timestamp, expected_reason, expected_timestamp_ms):
        """
        Rule 8: known historical exit reproduction
        """
        if replay_exit_reason != expected_reason:
            raise ReplayParityError(f"REPLAY_PARITY_FAIL: Exit reason mismatch. Expected {expected_reason}, got {replay_exit_reason}")
            
        if abs(replay_exit_timestamp - expected_timestamp_ms) > 60000:  # 1-minute tolerance
            raise ReplayParityError(f"REPLAY_PARITY_FAIL: Exit timestamp mismatch. Expected {expected_timestamp_ms}, got {replay_exit_timestamp}")

def assert_production_parity(position, open_trade, close_trade, guard_instance):
    """Call this before executing evaluate_peak_trailing in a replay script."""
    guard_instance.check_pairing(open_trade, close_trade)
    guard_instance.check_timestamp_parity(position)
    return True
