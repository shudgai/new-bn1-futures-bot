import re

def update_or_add(content, key, value):
    if re.search(f"^{key}=", content, flags=re.MULTILINE):
        return re.sub(f"^{key}=.*", f"{key}={value}", content, flags=re.MULTILINE)
    else:
        return content.rstrip() + f"\n{key}={value}\n"

with open(".env", "r") as f:
    content = f.read()

# Update Volume Ratio to 1.2
content = update_or_add(content, "KELTNER_MIN_VOLUME_RATIO", "1.2")

with open(".env", "w") as f:
    f.write(content)
