"""
core/meme_coin_strategy_config.py
=================================
妖幣專用量化交易策略獨立參數設定區（V2.0.0）。
- 與主流幣（BTC/ETH 等）完全隔離，僅對 is_meme_breakout_symbol(symbol) 生效。
- 所有參數皆有合理上下限與版本追蹤，缺少必要資料時採 Fail-Closed。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

STRATEGY_VERSION = "2.0.0"

# =====================================
# MEME COIN STRATEGY CONFIG (初始候選值)
# =====================================
HTF_SLOPE_THRESHOLD = 0.025
LTF_SHORT_SLOPE_MIN = 0.03
LTF_LONG_SLOPE_MIN = 0.08

MAX_ENTRY_BAND_RATIO = 1.50
MAX_WICK_RATIO = 0.45

ABNORMAL_SINGLE_ATR_MULT = 1.3
ABNORMAL_MULTI_ATR_MULT = 2.0
ABNORMAL_PCT_THRESHOLD = 0.007

CIRCUIT_BREAKER_COOLDOWN_SEC = 420

HARD_SL_ATR_MULT = 1.6
BREAKEVEN_START_ATR = 0.8
TRAILING_START_ATR = 1.2
TRAILING_DIST_ATR = 0.7
FLAT_EXIT_BUFFER_ATR = 0.25


@dataclass(frozen=True)
class MemeCoinParams:
    version: str = STRATEGY_VERSION
    htf_slope_threshold: float = HTF_SLOPE_THRESHOLD
    ltf_short_slope_min: float = LTF_SHORT_SLOPE_MIN
    ltf_long_slope_min: float = LTF_LONG_SLOPE_MIN
    max_entry_band_ratio: float = MAX_ENTRY_BAND_RATIO
    max_wick_ratio: float = MAX_WICK_RATIO
    abnormal_single_atr_mult: float = ABNORMAL_SINGLE_ATR_MULT
    abnormal_multi_atr_mult: float = ABNORMAL_MULTI_ATR_MULT
    abnormal_pct_threshold: float = ABNORMAL_PCT_THRESHOLD
    circuit_breaker_cooldown_sec: int = CIRCUIT_BREAKER_COOLDOWN_SEC
    hard_sl_atr_mult: float = HARD_SL_ATR_MULT
    breakeven_start_atr: float = BREAKEVEN_START_ATR
    trailing_start_atr: float = TRAILING_START_ATR
    trailing_dist_atr: float = TRAILING_DIST_ATR
    flat_exit_buffer_atr: float = FLAT_EXIT_BUFFER_ATR

    def __post_init__(self) -> None:
        if not (0.005 <= self.htf_slope_threshold <= 0.2):
            raise ValueError("htf_slope_threshold out of bounds [0.005, 0.2]")
        if not (0.01 <= self.ltf_short_slope_min <= 0.5):
            raise ValueError("ltf_short_slope_min out of bounds [0.01, 0.5]")
        if not (0.02 <= self.ltf_long_slope_min <= 1.0):
            raise ValueError("ltf_long_slope_min out of bounds [0.02, 1.0]")
        if not (0.3 <= self.max_entry_band_ratio <= 2.5):
            raise ValueError("max_entry_band_ratio out of bounds [0.3, 2.5]")
        if not (0.1 <= self.max_wick_ratio <= 0.8):
            raise ValueError("max_wick_ratio out of bounds [0.1, 0.8]")
        if not (0.8 <= self.abnormal_single_atr_mult <= 4.0):
            raise ValueError("abnormal_single_atr_mult out of bounds [0.8, 4.0]")
        if not (1.2 <= self.abnormal_multi_atr_mult <= 6.0):
            raise ValueError("abnormal_multi_atr_mult out of bounds [1.2, 6.0]")
        if not (0.002 <= self.abnormal_pct_threshold <= 0.05):
            raise ValueError("abnormal_pct_threshold out of bounds [0.002, 0.05]")
        if not (60 <= self.circuit_breaker_cooldown_sec <= 3600):
            raise ValueError("circuit_breaker_cooldown_sec out of bounds [60, 3600]")
        if not (0.8 <= self.hard_sl_atr_mult <= 5.0):
            raise ValueError("hard_sl_atr_mult out of bounds [0.8, 5.0]")
        if not (0.3 <= self.breakeven_start_atr <= 3.0):
            raise ValueError("breakeven_start_atr out of bounds [0.3, 3.0]")
        if not (0.5 <= self.trailing_start_atr <= 4.0):
            raise ValueError("trailing_start_atr out of bounds [0.5, 4.0]")
        if not (0.2 <= self.trailing_dist_atr <= 2.0):
            raise ValueError("trailing_dist_atr out of bounds [0.2, 2.0]")
        if not (0.05 <= self.flat_exit_buffer_atr <= 1.0):
            raise ValueError("flat_exit_buffer_atr out of bounds [0.05, 1.0]")


DEFAULT_MEME_PARAMS = MemeCoinParams()

PER_SYMBOL_OVERRIDES: Dict[str, MemeCoinParams] = {
    "1000PEPE/USDT": DEFAULT_MEME_PARAMS,
    "龙虾/USDT": DEFAULT_MEME_PARAMS,
    "LOBSTER/USDT": DEFAULT_MEME_PARAMS,
    "CAP/USDT": DEFAULT_MEME_PARAMS,
}


def get_meme_params(symbol: str | None) -> MemeCoinParams:
    """Return validated meme-coin parameters for the given symbol."""
    if not symbol:
        return DEFAULT_MEME_PARAMS
    return PER_SYMBOL_OVERRIDES.get(str(symbol).strip().upper(), DEFAULT_MEME_PARAMS)
