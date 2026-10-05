"""Observe account memory without refreshing, managing stops or placing orders."""
import time


def account_exposure_snapshot(account, *, paper_trading, use_testnet, is_running):
    positions = getattr(account, 'positions', {})
    pending = getattr(account, 'pending_limit_orders', {})
    mode = 'paper' if paper_trading else 'testnet' if use_testnet else 'live'
    return {
        'observed_at_ms': int(time.time() * 1000),
        'mode': mode,
        'is_running': bool(is_running),
        'source': 'runtime_account_memory',
        'verification': 'VERIFIED' if paper_trading else 'UNVERIFIED',
        'position_count': len(positions),
        'position_symbols': sorted(positions),
        'pending_count': len(pending),
        'pending_symbols': sorted(pending),
        'exchange_authoritative': False,
    }
