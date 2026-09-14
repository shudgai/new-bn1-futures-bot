import re

with open("core/engine.py", "r") as f:
    content = f.read()

abnormal_logic = """
            recent_bodies = [abs(float(row["close"]) - float(row["open"])) for _, row in frame.iloc[-10:-1].iterrows()]
            avg_body = sum(recent_bodies) / len(recent_bodies) if recent_bodies else 0
            live_body = abs(live_price - live_open)
            is_abnormal = (live_body >= kc_width * 0.8) or (avg_body > 0 and live_body >= avg_body * 3)
            
            # 除了Kc裡都是同色K時,突破就馬上開倉 (只限於異常K及大瀑布)
            if live_price > live_upper and trend > 0 and TradingEngine._channel_all_same_color_inside(frame, "LONG") and is_abnormal:
                return {"action": "ENTER", "side": "LONG", "reason": "LIVE_UPPER_BREAKOUT_ABNORMAL"}
            if live_price < live_lower and trend < 0 and TradingEngine._channel_all_same_color_inside(frame, "SHORT") and is_abnormal:
                return {"action": "ENTER", "side": "SHORT", "reason": "LIVE_LOWER_BREAKOUT_ABNORMAL"}
"""

old_logic = """
            # 除了Kc裡都是同色K時,突破就馬上開倉
            if live_price > live_upper and trend > 0 and TradingEngine._channel_all_same_color_inside(frame, "LONG"):
                return {"action": "ENTER", "side": "LONG", "reason": "LIVE_UPPER_BREAKOUT"}
            if live_price < live_lower and trend < 0 and TradingEngine._channel_all_same_color_inside(frame, "SHORT"):
                return {"action": "ENTER", "side": "SHORT", "reason": "LIVE_LOWER_BREAKOUT"}
"""

if old_logic.strip() in content:
    content = content.replace(old_logic.strip(), abnormal_logic.strip())
    with open("core/engine.py", "w") as f:
        f.write(content)
    print("Replaced successfully")
else:
    print("Could not find old logic")
