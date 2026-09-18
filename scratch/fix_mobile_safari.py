import re

with open("web/index.html", "r", encoding="utf-8") as f:
    content = f.read()

# Replace bot.positions.find(...)?. with getPos(symbol) safely
content = re.sub(
    r"bot\.positions\.find\(p => p\.symbol === symbol\)\?\.", 
    r"(getPos(symbol) || {}).", 
    content
)

# Replace other ?. in template
content = re.sub(r"chartModal\.states\[symbol\]\?\.", r"(chartModal.states[symbol] || {}).", content)

# Replace JS ?.
content = re.sub(r"entries\[0\]\?\.", r"(entries[0] || {}).", content)
content = re.sub(r"json\.cache\?\.", r"(json.cache || {}).", content)
content = re.sub(r"candles\[candles\.length - 1\]\?\.", r"(candles[candles.length - 1] || {}).", content)
content = re.sub(r"this\.bot\?\.", r"(this.bot || {}).", content)
content = re.sub(r"ctx\?\.", r"(ctx || {}).", content)

with open("web/index.html", "w", encoding="utf-8") as f:
    f.write(content)
print("Done")
