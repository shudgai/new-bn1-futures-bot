"""Single source of truth for trading-symbol alias groups.

Exchanges and dashboards may report the same contract with a ``:USDT``
settlement suffix or a Simplified/Traditional Chinese name. Every gate that
grants symbol-specific behaviour must consult these sets instead of keeping a
private list, so an alias can never silently fall back to the generic rules.
"""
from typing import Optional

MEME_BREAKOUT_SYMBOLS = frozenset((
    'CAP/USDT', 'CAP/USDT:USDT',
    '1000PEPE/USDT', '1000PEPE/USDT:USDT', '1000PEPEUSDT',
    'PEPE/USDT', 'PEPE/USDT:USDT', 'PEPEUSDT',
    '龍蝦/USDT', '龍蝦/USDT:USDT', '龙虾/USDT', '龙虾/USDT:USDT',
    'LOBSTER/USDT', 'LOBSTER/USDT:USDT',
))

PIVOT_ONLY_CHANNEL_SYMBOLS = frozenset((
    'SUI/USDT', 'SUI/USDT:USDT',
    # CAP and Lobster share the same strict entry and exit policy.
    'CAP/USDT', 'CAP/USDT:USDT',
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


def is_auto_entry_symbol(symbol: Optional[str]) -> bool:
    """Return whether automatic and manual entry is currently enabled."""
    return is_meme_breakout_symbol(symbol)


def is_cap_symbol(symbol: Optional[str]) -> bool:
    """Return whether a symbol is one of the CAP contract aliases."""
    return normalize_symbol(symbol) in {'CAP/USDT', 'CAP/USDT:USDT'}


def is_pepe_symbol(symbol: Optional[str]) -> bool:
    """Return whether a symbol is one of the 1000PEPE contract aliases."""
    return normalize_symbol(symbol) in {
        '1000PEPE/USDT', '1000PEPE/USDT:USDT', '1000PEPEUSDT',
        'PEPE/USDT', 'PEPE/USDT:USDT', 'PEPEUSDT',
    }


CANONICAL_LOBSTER_SYMBOL = '龙虾/USDT'
CANONICAL_CAP_SYMBOL = 'CAP/USDT'


def to_binance_usdm_symbol(symbol: str) -> str:
    """Map internal display aliases to Binance USD-M perpetual symbols."""
    if is_lobster_symbol(symbol):
        return '龙虾/USDT:USDT'
    if is_cap_symbol(symbol):
        return 'CAP/USDT:USDT'
    if is_pepe_symbol(symbol):
        return '1000PEPE/USDT:USDT'
    return str(symbol).strip()


def to_canonical_display_symbol(symbol: Optional[str]) -> str:
    """Map any alias to standard internal display symbol (e.g. 龙虾/USDT)."""
    if is_lobster_symbol(symbol):
        return CANONICAL_LOBSTER_SYMBOL
    if is_cap_symbol(symbol):
        return CANONICAL_CAP_SYMBOL
    return str(symbol or '').strip()


def is_pivot_only_channel_symbol(symbol: Optional[str]) -> bool:
    """Return whether Channel Swing exits are restricted to pivot/ROE triggers."""
    return normalize_symbol(symbol) in PIVOT_ONLY_CHANNEL_SYMBOLS


def is_chop_filter_symbol(symbol: Optional[str]) -> bool:
    """Return whether a symbol is subject to consolidation / chop filtering."""
    return normalize_symbol(symbol) in CHOP_FILTER_SYMBOLS
