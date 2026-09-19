import re

with open('core/services/strategies/unified_entry_strategy.py', 'r') as f:
    content = f.read()

# Change signature
content = content.replace(
    'def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:',
    'def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str, dict]:'
)

# Update return values to return an empty dict by default if false
content = re.sub(r'return False, (.*)', r'return False, \1, {}', content)

# But wait, there are places with True. Let's find all returns.
