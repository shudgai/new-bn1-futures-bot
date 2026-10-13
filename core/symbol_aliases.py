"""Single source of truth for trading-symbol alias groups.

Exchanges and dashboards may report the same contract with a ``:USDT``
settlement suffix or a Simplified/Traditional Chinese name. Every gate that
grants symbol-specific behaviour must consult these sets instead of keeping a
private list, so an alias can never silently fall back to the generic rules.
"""
from typing import Optional

MEME_BREAKOUT_SYMBOLS = frozenset((
    'CAP/USDT', 'CAP/USDT:USDT',
    '龍蝦/USDT', '龍蝦/USDT:USDT', '龙虾/USDT', '龙虾/USDT:USDT',
    'LOBSTER/USDT', 'LOBSTER/USDT:USDT',
))

PIVOT_ONLY_CHANNEL_SYMBOLS = frozenset((
    'SUI/USDT', 'SUI/USDT:USDT',
)) | MEME_BREAKOUT_SYMBOLS

CHOP_FILTER_SYMBOLS = PIVOT_ONLY_CHANNEL_SYMBOLS


def normalize_symbol(symbol: Optional[str]) -> str:
    """Return a stripped upper-case symbol; Chinese names are unaffected."""
    return str(symbol or '').strip().upper()


def is_meme_breakout_symbol(symbol: Optional[str]) -> bool:
    """Return whether a supported meme coin uses the shared relaxed entry gates."""
    return normalize_symbol(symbol) in MEME_BREAKOUT_SYMBOLS


def is_lobster_symbol(symbol: Optional[str]) -> bool:
    """Return whether a symbol is one of the Lobster contract aliases."""
    return normalize_symbol(symbol) in {
        '龍蝦/USDT', '龍蝦/USDT:USDT', '龙虾/USDT', '龙虾/USDT:USDT',
        'LOBSTER/USDT', 'LOBSTER/USDT:USDT',
    }


def is_cap_symbol(symbol: Optional[str]) -> bool:
    """Return whether a symbol is one of the CAP contract aliases."""
    return normalize_symbol(symbol) in {'CAP/USDT', 'CAP/USDT:USDT'}


def is_pivot_only_channel_symbol(symbol: Optional[str]) -> bool:
    """Return whether Channel Swing exits are restricted to pivot/ROE triggers."""
    return normalize_symbol(symbol) in PIVOT_ONLY_CHANNEL_SYMBOLS


def is_chop_filter_symbol(symbol: Optional[str]) -> bool:
    """Return whether a symbol is subject to consolidation / chop filtering."""
    return normalize_symbol(symbol) in CHOP_FILTER_SYMBOLS
