#!/usr/bin/env python3
"""
tools/annotate_chart.py
=======================
在用戶上傳的 K 線圖上進行專業技術分析標註 (專業英數符號，避免無字型豆腐塊)：
- 綠色箭頭 (GREEN ARROW)：第一買點 BUY 1 (底部轉強 / Reclaim MA5)
- 藍色箭頭 (BLUE ARROW)：第二買點 BUY 2 (突破回踩 / Pullback Support)
- 紅色箭頭 (RED ARROW)：不宜追多 OVER-EXTENDED (高檔貼軌乖離 / High Risk)
- 紫色虛線 (PURPLE DASH)：重要停損參考或支撐位置 STOP LOSS 1 / 2
- 青色/橙色點線 (CYAN/AMBER DOTTED)：第一目標價與第二目標價 TARGET 1 / 2
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

input_path = '/home/shudgai999/.gemini/antigravity-ide/brain/49dc3fe8-8f79-45c7-b9d8-c29adf95d292/.user_uploaded/media_1792733535702.png'
img = mpimg.imread(input_path)

fig, ax = plt.subplots(figsize=(10, 9), dpi=220)
ax.imshow(img)

# 1. 第一買點 (綠色箭頭)：底部轉強 (x=112, y=320)
ax.annotate(
    "BUY 1: Bottom Reversal\n- Reclaim MA5 & Double Bottom\n- Break prior micro-swing high\n- Entry Area: Bull candle confirm",
    xy=(112, 320), xytext=(20, 440),
    arrowprops=dict(facecolor='#10b981', edgecolor='#047857', width=3.5, headwidth=11, shrink=0.08),
    bbox=dict(boxstyle="round,pad=0.5", fc="#ecfdf5", ec="#10b981", lw=1.5, alpha=0.95),
    fontsize=8.5, fontweight='bold', color='#065f46'
)

# 2. 第二買點 (藍色箭頭)：突破後回踩 (x=236, y=210)
ax.annotate(
    "BUY 2: Breakout Pullback\n- Penetrate KC Midline\n- Hold above rising MA5\n- Entry Area: Pullback support candle",
    xy=(236, 215), xytext=(90, 85),
    arrowprops=dict(facecolor='#3b82f6', edgecolor='#1d4ed8', width=3.5, headwidth=11, shrink=0.08),
    bbox=dict(boxstyle="round,pad=0.5", fc="#eff6ff", ec="#3b82f6", lw=1.5, alpha=0.95),
    fontsize=8.5, fontweight='bold', color='#1e40af'
)

# 3. 不宜追多位置 (紅色箭頭)：右上角貼緊 KC 上軌動能衰竭 (x=380, y=145)
ax.annotate(
    "DO NOT CHASE LONG (High Risk)\n- Touching KC Upper Rail\n- Momentum exhaustion & Pinbars\n- Low R:R / Vulnerable to pullback",
    xy=(380, 145), xytext=(305, 55),
    arrowprops=dict(facecolor='#ef4444', edgecolor='#b91c1c', width=3.5, headwidth=11, shrink=0.08),
    bbox=dict(boxstyle="round,pad=0.5", fc="#fef2f2", ec="#ef4444", lw=1.5, alpha=0.95),
    fontsize=8.5, fontweight='bold', color='#991b1b'
)

# 4. 重要支撐與停損參考 (紫色虛線)
# 停損參考 1：波谷最低點防守位 (y=380)
ax.axhline(y=380, xmin=0.08, xmax=0.45, color='#8b5cf6', linestyle='--', linewidth=2.2, alpha=0.9)
ax.text(50, 395, 'STOP LOSS 1: Valley Swing Low Support', color='#7c3aed', fontsize=8, fontweight='bold',
        bbox=dict(boxstyle="square,pad=0.2", fc="#f5f3ff", ec="#8b5cf6", alpha=0.9))

# 停損參考 2：突破後回踩支撐位 (y=245)
ax.axhline(y=245, xmin=0.28, xmax=0.55, color='#8b5cf6', linestyle='--', linewidth=2.2, alpha=0.9)
ax.text(170, 260, 'STOP LOSS 2: Pullback Swing Low Support', color='#7c3aed', fontsize=8, fontweight='bold',
        bbox=dict(boxstyle="square,pad=0.2", fc="#f5f3ff", ec="#8b5cf6", alpha=0.9))

# 5. 目標價區間 (青色/金色點線)
# 目標 1：左側前波高點水準線 (y=155)
ax.axhline(y=155, xmin=0.02, xmax=0.68, color='#06b6d4', linestyle=':', linewidth=2.0, alpha=0.85)
ax.text(15, 172, 'TARGET 1: Prior Swing High Resistance', color='#0891b2', fontsize=8, fontweight='bold',
        bbox=dict(boxstyle="square,pad=0.2", fc="#ecfeff", ec="#06b6d4", alpha=0.9))

# 目標 2：KC 上軌擴張波段滿足區 (y=125)
ax.axhline(y=125, xmin=0.45, xmax=0.72, color='#f59e0b', linestyle=':', linewidth=2.0, alpha=0.85)
ax.text(280, 115, 'TARGET 2: KC Upper Rail Extension', color='#d97706', fontsize=8, fontweight='bold',
        bbox=dict(boxstyle="square,pad=0.2", fc="#fffbeb", ec="#f59e0b", alpha=0.9))

ax.axis('off')
plt.title("1M K-Line Technical Structure & Long Entry Opportunities (Annotated)", fontsize=13, fontweight='bold', pad=12)

output_path = '/home/shudgai999/.gemini/antigravity-ide/brain/49dc3fe8-8f79-45c7-b9d8-c29adf95d292/lobster_technical_analysis_annotated.png'
plt.tight_layout()
plt.savefig(output_path, bbox_inches='tight', dpi=220)
plt.close()

print(f"Annotated chart successfully saved to: {output_path}")
