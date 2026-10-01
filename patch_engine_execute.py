with open("core/engine.py", "r") as f:
    content = f.read()

import re

# Remove the old `evaluate_closed_entry` double check in `_execute_confirmed_channel_break`
new_func = """    async def _execute_confirmed_channel_break(self, symbol, frame, price, side, daily_halt=False, v8_reason=None, size_fraction=1.):
        from core.services.strategies.unified_entry_strategy import confirmed
        if daily_halt or symbol in self.account.positions:
            self.account.log(f'🛑 [ENTRY_GATE_FAIL] {symbol} _execute_confirmed_channel_break early check 1 failed: daily_halt={daily_halt} in_pos={symbol in self.account.positions}', 'WARNING')
            return False
            
        closed = confirmed(frame)
        if closed is None or closed.empty:
            return False
            
        candidate_bar_id = closed.iloc[-1]['timestamp']
        reason = v8_reason or "PURE_TREND_V2"
        
        signal = dict(side=side,score=100,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',
                      signal_code=reason,candidate_bar_id=candidate_bar_id,
                      size_fraction=size_fraction)
        return await self._place_structured_entry(symbol,signal,price)"""

content = re.sub(r"    async def _execute_confirmed_channel_break\(self, symbol, frame, price, side, daily_halt=False, v8_reason=None, size_fraction=1\.\):.*?return await self\._place_structured_entry\(symbol,signal,price\)",
                 new_func, content, flags=re.DOTALL)

with open("core/engine.py", "w") as f:
    f.write(content)
