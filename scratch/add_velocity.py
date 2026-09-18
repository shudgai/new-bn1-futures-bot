import re

with open("core/engine.py", "r") as f:
    content = f.read()

velocity_method = """    def get_velocity_slowdown(self, symbol: str) -> bool:
        \"\"\"
        V5.0 極致點位捕捉：判斷 Tick 變動速度是否放緩
        計算連續 3 Tick 的均速是否低於前 5 Tick 的均速 30% 以上
        \"\"\"
        buffer = self.tick_buffers.get(symbol)
        if not buffer or len(buffer) < 8:
            return False
            
        ticks = list(buffer)
        
        def avg_speed(tick_list):
            if len(tick_list) < 2: return 0
            total_time = (tick_list[-1][0] - tick_list[0][0]) / 1000.0  # seconds
            total_dist = sum(abs(tick_list[i][1] - tick_list[i-1][1]) for i in range(1, len(tick_list)))
            if total_time <= 0: return 0
            return total_dist / total_time
            
        recent_3 = ticks[-3:]
        prev_5 = ticks[-8:-3]
        
        speed_recent = avg_speed(recent_3)
        speed_prev = avg_speed(prev_5)
        
        if speed_prev > 0:
            drop_ratio = (speed_prev - speed_recent) / speed_prev
            return drop_ratio >= 0.30
        return False
        
    async def _place_structured_entry(
"""

content = content.replace("    async def _place_structured_entry(\n", velocity_method)

with open("core/engine.py", "w") as f:
    f.write(content)
print("Updated core/engine.py")
