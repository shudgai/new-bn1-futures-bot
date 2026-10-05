from enum import Enum


class PositionState(Enum):
    IDLE = "IDLE"
    LONG = "LONG"
    SHORT = "SHORT"

class StrictStateMachineStrategy:
    def __init__(self):
        # 記錄各幣種的當前狀態，預設為空手
        self.states = {}
        # 記錄持倉資訊（如進場價、最高浮盈）
        self.positions = {}
        # 記錄最後平倉的 timestamp，用於冷卻機制
        self.last_exits = {}

    def get_state(self, symbol: str) -> PositionState:
        return self.states.get(symbol, PositionState.IDLE)

    def set_state(self, symbol: str, state: PositionState, position_data: dict = None):
        self.states[symbol] = state
        if state == PositionState.IDLE:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = position_data or {}


    def evaluate_tick(self, symbol: str, frame, live_price: float, unrealized_pnl: float = 0.0):
        """
        DEPRECATED / UNUSED: 
        This method is historically disconnected from the main loop and does not act as an exit authority.
        Exits are exclusively managed by evaluate_peak_trailing() and update_trailing_stops().
        """
        if self.get_state(symbol) == PositionState.IDLE:
            from core.services.entry_contract import evaluate_entry_contract
            diagnostics = {}
            entry = evaluate_entry_contract(frame, live_price, symbol=symbol, diagnostics=diagnostics)
            return ({'action': 'ENTER_' + entry['side'], 'reason': entry['type']} if entry
                    else {'action': 'WAIT', 'reason': diagnostics.get('reason', 'WAIT_VALID_ENTRY_DATA')})
        # This compatibility interface has no independent MA exit authority.
        return {'action': 'WAIT', 'reason': 'MANAGED_BY_SHARED_EXIT_POLICY'}
