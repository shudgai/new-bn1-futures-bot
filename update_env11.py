import re

def update_or_add(content, key, value):
    if re.search(f"^{key}=", content, flags=re.MULTILINE):
        return re.sub(f"^{key}=.*", f"{key}={value}", content, flags=re.MULTILINE)
    else:
        return content.rstrip() + f"\n{key}={value}\n"

with open(".env", "r") as f:
    content = f.read()

# Proportional trailing config
content = update_or_add(content, "TRAILING_STOP_ACTIVATION_PCT", "0.0020")
content = update_or_add(content, "TRAILING_STOP_RETAIN_PCT", "0.70")
content = update_or_add(content, "TRAILING_CALLBACK_PCT", "0.0100") # Disable fixed callback interference

with open(".env", "w") as f:
    f.write(content)
