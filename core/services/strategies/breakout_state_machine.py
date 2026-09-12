"""CK 通道突破確認與延遲開倉狀態機（使用者 2026-09-14 最終規格）。

規格重點：
1. 所有信號以「K 線收盤確認（Bar Closed）」為準，不使用未收盤的即時跳動價。
2. 第 1 根突破：強實體（實體佔比 >= body_threshold）且收盤穿出 CK 外軌。
3. 突破後等待：同色強實體 → 開倉；同色弱體 → 順延等待；反向色 → 訊號作廢。
4. 持倉時若出現反向突破 → 先市價平倉，並把該根當作反向的第 1 根突破（pending 設為反向）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

LONG = "LONG"
SHORT = "SHORT"


@dataclass
class Bar:
    """單根已收線 K 棒。"""

    open: float
    high: float
    low: float
    close: float
    kc_upper: float
    kc_lower: float

    @property
    def span(self) -> float:
        return float(self.high) - float(self.low)

    @property
    def body(self) -> float:
        return abs(float(self.close) - float(self.open))

    @property
    def body_ratio(self) -> float:
        return self.body / self.span if self.span > 0 else 0.0

    @property
    def is_bull(self) -> bool:
        return float(self.close) > float(self.open)

    @property
    def is_bear(self) -> bool:
        return float(self.close) < float(self.open)

    def is_solid(self, body_threshold: float) -> bool:
        return self.body_ratio >= float(body_threshold)


@dataclass
class BreakoutState:
    """每個交易對各自的狀態。"""

    pending_breakout: Optional[str] = None
    position: Optional[str] = None
    notes: list = field(default_factory=list)


def classify(bar: Bar, body_threshold: float) -> str:
    """回傳 'BULL_BREAK' / 'BEAR_BREAK' / 'BULL' / 'BEAR' / 'FLAT'。"""
    if bar.is_bull and bar.is_solid(body_threshold) and float(bar.close) > float(bar.kc_upper):
        return "BULL_BREAK"
    if bar.is_bear and bar.is_solid(body_threshold) and float(bar.close) < float(bar.kc_lower):
        return "BEAR_BREAK"
    if bar.is_bull:
        return "BULL"
    if bar.is_bear:
        return "BEAR"
    return "FLAT"


def on_bar_closed(state: BreakoutState, bar: Bar, body_threshold: float = 0.5) -> Optional[str]:
    """處理一根已收線 K 棒，回傳要執行的動作：None / 'OPEN_LONG' / 'OPEN_SHORT' / 'CLOSE_LONG' / 'CLOSE_SHORT'。

    嚴格依規格順序：先處理持倉平倉（含特例翻轉），再處理無倉位時的第 1 根突破。
    """
    kind = classify(bar, body_threshold)

    # 步驟一：持倉中遇到反向突破 → 先平倉，並把該根當作反向的第 1 根突破。
    if state.position == LONG and kind == "BEAR_BREAK":
        state.position = None
        state.pending_breakout = SHORT
        state.notes.append("持多遇空頭強突破：平多並將本根視為做空第1根")
        return "CLOSE_LONG"
    if state.position == SHORT and kind == "BULL_BREAK":
        state.position = None
        state.pending_breakout = LONG
        state.notes.append("持空遇多頭強突破：平空並將本根視為做多第1根")
        return "CLOSE_SHORT"

    # 步驟二：無倉位且沒有等待中的突破 → 記錄第 1 根突破（當根不開倉）。
    if state.position is None and state.pending_breakout is None:
        if kind == "BULL_BREAK":
            state.pending_breakout = LONG
            state.notes.append("多頭突破第1根：等待後續確認")
        elif kind == "BEAR_BREAK":
            state.pending_breakout = SHORT
            state.notes.append("空頭突破第1根：等待後續確認")
        return None

    # 步驟三：等待確認（同色強實體開倉／同色弱體順延／反向色作廢）。
    if state.pending_breakout == LONG and state.position is None:
        if kind == "BULL_BREAK" or (kind == "BULL" and bar.is_solid(body_threshold)):
            state.pending_breakout = None
            state.position = LONG
            return "OPEN_LONG"
        if kind == "BULL":
            state.notes.append("多頭弱體：順延等待")
            return None
        state.pending_breakout = None
        state.notes.append("出現反向色：多頭信號作廢")
        return None

    if state.pending_breakout == SHORT and state.position is None:
        if kind == "BEAR_BREAK" or (kind == "BEAR" and bar.is_solid(body_threshold)):
            state.pending_breakout = None
            state.position = SHORT
            return "OPEN_SHORT"
        if kind == "BEAR":
            state.notes.append("空頭弱體：順延等待")
            return None
        state.pending_breakout = None
        state.notes.append("出現反向色：空頭信號作廢")
        return None

    return None
