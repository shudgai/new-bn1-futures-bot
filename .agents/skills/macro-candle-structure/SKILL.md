---
name: macro-candle-structure
description: 全視角 K 線結構審查機能 (MacroCandleStructureGate)。提供 AI 通盤審查能力，透過通道相對位置、均線乖離率與 K 棒形態，在開倉前進行強制結構性審查並行使一票否決權 (VETO)，防止天花板追多或地板追空。
---

# 全視角 K 線結構審查機能 (Macro Candle Structure Skill)

## 1. 機能定位與目的
- **核心目的**：防止交易機器人或 AI 決策在單邊行情末端出現「地板追空」或「天花板追多」。
- **決策權限**：在均線金叉/死叉（MA5/MA15）或指標發出開倉候選訊號時，本機能擁有最高優先級的 **「一票否決權（VETO）」**。

---

## 2. 三大核心審查流程（必須全部通過才放行）

### 一、 通道相對位置審查 (KC Relative Location)
- **多單（買多）**：
  * 現價必須位於通道下軌附近：$KC\_Position = \frac{close - kc\_lower}{kc\_upper - kc\_lower} \le 0.35$。
  * 若價格已衝至中軌上方（$KC\_Position > 0.35$），判定為天花板追高，**100% 絕對一票否決**。
- **空單（賣空）**：
  * 現價必須位於通道上軌附近：$KC\_Position = \frac{close - kc\_lower}{kc\_upper - kc\_lower} \ge 0.65$。
  * 若價格已跌至中軌下方（$KC\_Position < 0.65$），判定為地板追空，**100% 絕對一票否決**。

---

### 二、 均線乖離審查 (MA15 Bias / Extension)
- **乖離倍數計算**：
  $$Bias\_ATR = \frac{|close - ma15|}{ATR}$$
- **過度延伸否決**：
  * 當 $Bias\_ATR > 1.2$ 倍 ATR 時，視為動能過度透支延伸，隨時面臨強烈回抽。
  * 一律嚴格禁止順向追單，觸發 `VETO: 乖離過大`。

---

### 三、 K 棒形態衰竭審查 (Candle Exhaustion & Reversal)
- **做多（買多）衰竭否決**：
  * 頂部遇阻留長上影線（$upper\_wick > body$ 且 $close < high$），表示上方賣壓沉重、多頭受阻滯漲，**一律禁止開多**。
- **做空（賣空）衰竭否決**：
  * 底部爆量留長下影線（$lower\_wick > body$ 且 $close > low$），表示下方買盤強力托底抵抗、空頭見底，**一律禁止開空**。

---

## 3. 程式碼引用與管線集成
```python
from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

macro_gate = MacroCandleStructureGate(
    kc_pos_long_max=0.35,
    kc_pos_short_min=0.65,
    max_bias_atr=1.2
)

# 在開倉訊號授權前執行前置審查
can_open, reason = macro_gate.can_open(side, closed_bar, indicators)
if not can_open:
    # 一票否決開倉
    return False, reason
```
