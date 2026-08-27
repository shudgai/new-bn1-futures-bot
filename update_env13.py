import re

def update_or_add(content, key, value):
    if re.search(f"^{key}=", content, flags=re.MULTILINE):
        return re.sub(f"^{key}=.*", f"{key}={value}", content, flags=re.MULTILINE)
    else:
        return content.rstrip() + f"\n{key}={value}\n"

with open(".env", "r") as f:
    content = f.read()

# Update slots to 3
content = update_or_add(content, "MAX_SLOTS", "3")
content = update_or_add(content, "PYRAMID_MAX_SLOTS", "3")

with open(".env", "w") as f:
    f.write(content)
