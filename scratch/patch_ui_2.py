import re

with open("web/index.html", "r") as f:
    content = f.read()

# Fix the method to return string or number
new_func = """                getLockedProfitAmount(pos) {
                    // 優先抓取真實階梯鎖利的保護值
                    if (pos && pos.channel_profit_protection && pos.channel_profit_protection.locked_net > 0) {
                        return pos.channel_profit_protection.locked_net;
                    }
                    return null; // 若未啟動鎖利，回傳 null
                },"""

content = re.sub(r'                getLockedProfitAmount\(pos\) \{.*?                \},', new_func, content, flags=re.DOTALL)

# Now fix the display in HTML
# There are two places: line 389 and 622
content = content.replace(
    """🔒 真正的鎖利: {{ getLockedProfitAmount(pos) > 0 ? '+' : '' }}{{ getLockedProfitAmount(pos).toFixed(4) }} USDT""",
    """🔒 真正的鎖利: {{ getLockedProfitAmount(pos) !== null ? '+' + getLockedProfitAmount(pos).toFixed(4) + ' USDT' : '未啟動' }}"""
)

content = content.replace(
    """<span class="chart-pos-info-label" :style="getLockedProfitAmount(getPos(symbol)) > 0 ? 'color: #22c55e; font-weight: bold;' : 'color: #ef4444; font-weight: bold;'">🔒真正的鎖利:</span>""",
    """<span class="chart-pos-info-label" :style="getLockedProfitAmount(getPos(symbol)) !== null ? 'color: #22c55e; font-weight: bold;' : 'color: #94a3b8; font-weight: bold;'">��真正的鎖利:</span>"""
)

content = content.replace(
    """<span class="chart-pos-info-value" :style="getLockedProfitAmount(getPos(symbol)) > 0 ? 'color:#22c55e; font-family:monospace; font-weight:bold;' : 'color:#ef4444; font-family:monospace; font-weight:bold;'">
                                    {{ getLockedProfitAmount(getPos(symbol)) > 0 ? '+' : '' }}{{ getLockedProfitAmount(getPos(symbol)).toFixed(4) }} USDT
                                </span>""",
    """<span class="chart-pos-info-value" :style="getLockedProfitAmount(getPos(symbol)) !== null ? 'color:#22c55e; font-family:monospace; font-weight:bold;' : 'color:#94a3b8; font-family:monospace; font-weight:bold;'">
                                    {{ getLockedProfitAmount(getPos(symbol)) !== null ? '+' + getLockedProfitAmount(getPos(symbol)).toFixed(4) + ' USDT' : '未啟動' }}
                                </span>"""
)

with open("web/index.html", "w") as f:
    f.write(content)
