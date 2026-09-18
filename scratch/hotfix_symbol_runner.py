import re

with open("core/services/symbol_runner.py", "r") as f:
    content = f.read()

content = content.replace(
    "from core.engine import get_velocity_slowdown",
    ""
)
content = content.replace(
    "velocity_slowdown = get_velocity_slowdown(engine.tick_buffers.get(symbol, []))",
    "velocity_slowdown = engine.get_velocity_slowdown(symbol)"
)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content)
