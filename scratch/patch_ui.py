import re

with open("web/index.html", "r") as f:
    content = f.read()

old_func = """                getLockedProfitAmount(pos) {
                    // 使用獨立的顯示欄位，不干擾硬停損 sl
                    const displaySl = pos && pos.profit_lock_display_sl;
                    if (!displaySl || displaySl === 0) return this.getEstimatedLossAtSL(pos);
                    return this.getEstimatedPnlAt(pos, displaySl);
                },"""

new_func = """                getLockedProfitAmount(pos) {
                    // 優先抓取真實階梯鎖利的保護值
                    if (pos && pos.channel_profit_protection && pos.channel_profit_protection.locked_net > 0) {
                        return pos.channel_profit_protection.locked_net;
                    }
                    // 使用獨立的顯示欄位，不干擾硬停損 sl
                    const displaySl = pos && pos.profit_lock_display_sl;
                    if (!displaySl || displaySl === 0) return this.getEstimatedLossAtSL(pos);
                    return this.getEstimatedPnlAt(pos, displaySl);
                },"""

content = content.replace(old_func, new_func)

with open("web/index.html", "w") as f:
    f.write(content)
