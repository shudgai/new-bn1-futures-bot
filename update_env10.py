import re

def update_or_add(content, key, value):
    if re.search(f"^{key}=", content, flags=re.MULTILINE):
        return re.sub(f"^{key}=.*", f"{key}={value}", content, flags=re.MULTILINE)
    else:
        return content.rstrip() + f"\n{key}={value}\n"

with open(".env", "r") as f:
    content = f.read()

# Update callback to 0.3% (0.0030) for smaller market swings
content = update_or_add(content, "TRAILING_CALLBACK_PCT", "0.0030")

with open(".env", "w") as f:
    f.write(content)
