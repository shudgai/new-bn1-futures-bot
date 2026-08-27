import re

def update_or_add(content, key, value):
    if re.search(f"^{key}=", content, flags=re.MULTILINE):
        return re.sub(f"^{key}=.*", f"{key}={value}", content, flags=re.MULTILINE)
    else:
        return content.rstrip() + f"\n{key}={value}\n"

with open(".env", "r") as f:
    content = f.read()

# Update RSI thresholds to 30 and 70
content = update_or_add(content, "RSI_LONG_THRESHOLD", "30")
content = update_or_add(content, "RSI_SHORT_THRESHOLD", "70")

with open(".env", "w") as f:
    f.write(content)
