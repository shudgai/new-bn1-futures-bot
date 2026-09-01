# 交易機器人改進摘要 (2024-08-28)

## 概述
根據用戶需求實現了3項關鍵改進，提升交易決策的精準度和持倉管理能力。

---

## 改進 #1：黑圈處平倉轉向（強勢多單檢測）

### 需求
- 當綠K從中軌衝到外軌外（黑圈位置），這是很清楚的強勢多單信號
- 若此時有空單持倉，應立即平倉轉向
- 若無持倉，應快速開倉

### 實現
**文件修改：** `core/engine.py` 的 `_process_single_symbol()` 函數

**核心邏輯：**
```python
# 第一步：檢測強勢多單訊號（綠K衝外軌）
from core.strategy import detect_strong_green_candle_burst
df_1m_signal = await self.fetch_klines(symbol, timeframe="1m", limit=30)
strong_burst = detect_strong_green_candle_burst(df_1m_signal)

if strong_burst.get("detected"):
    # 若有空單，立即平倉
    if symbol in self.account.positions and pos.get("side") == "SHORT":
        await self.account.close_position(
            symbol=symbol,
            current_price=live_price,
            close_reason="黑圈位：強勢多單訊號，平倉空單準備轉向"
        )
    
    # 標記為進場候選
    detected_candidates.append({
        "symbol": symbol,
        "side": "LONG",
        "score": 95,
        "entry_mode": "STRONG_LONG_BURST",
        ...
    })
```

**特點：**
- 使用1m時間框架快速檢測
- 優先級最高（返回前直接結束該幣種的其他檢測）
- 自動平倉空單，避免人工延遲
- 標記為強勢信號，進場分數設為95分

---

## 改進 #2：多單在外軌持有到最高點

### 需求
- 多單若在外軌（衝過KC上軌），應一路抱著直到最高點出現
- 平倉後若已進入峰谷區間，應不再追進新倉
- 避免過早平倉導致獲利吐回

### 實現
**文件修改：** `core/engine.py` 的 `_process_single_symbol()` 函數

**核心邏輯：**
```python
# 第二步：多單在外軌時，不要提早出場（延後平倉邏輯）
if symbol in self.account.positions:
    pos = self.account.positions[symbol]
    if pos.get("side") == "LONG":
        df_check_outer = await self.fetch_klines(symbol, timeframe="1m", limit=20)
        curr_close = float(df_check_outer['close'].iloc[-1])
        kc_upper = float(df_check_outer['kc_upper'].iloc[-1])
        
        is_in_outer_rail = curr_close > kc_upper
        if is_in_outer_rail:
            # 記錄最高價格用於追蹤
            pos_meta = self.account.position_meta.setdefault(symbol, {})
            peak_price = pos_meta.get("outer_rail_peak_price", curr_close)
            if curr_close > peak_price:
                pos_meta["outer_rail_peak_price"] = curr_close
                pos_meta["outer_rail_peak_time"] = now_time
            
            # 暫停此次的平倉檢查，繼續持有
            return signal_progress, detected_candidates
```

**持倉跟踪機制：**
- `outer_rail_peak_price` - 記錄外軌內的最高價格
- `outer_rail_peak_time` - 記錄達到最高價格的時間
- 當價格創新高時自動更新峰值
- 保留記錄供出場決策參考

**特點：**
- 在外軌時主動跳過本輪檢測，避免技術型平倉觸發
- 持倉管理迴圈（stop loss/trailing stop）仍保持獨立運作
- 防止「在最高點前出場」的遺憾

---

## 改進 #3：MA15 線條顏色改為咖啡色

### 需求
- 將圖表中的 MA15 線條改為咖啡色，便於視覺識別

### 實現
**文件修改：** `web/index.html` 第 710 行

```javascript
// 修改前
this._ma15Series = this._chart.addLineSeries({ color: '#A67C52', lineWidth: 2, title: 'MA15' });

// 修改後
this._ma15Series = this._chart.addLineSeries({ color: '#8B6F47', lineWidth: 2, title: 'MA15' });
```

**顏色說明：**
- `#8B6F47` - 深咖啡色（Darkwood）
- 相比前色 `#A67C52` 更深，視覺識別度更高
- 與其他線條（MA3 粉紅色#E91E63、MA99 紫色#9C27B0）形成明確對比

---

## 改進 #4：增強的強勢多單檢測函數

### 文件修改
**文件修改：** `core/strategy.py` 的 `detect_strong_green_candle_burst()` 函數

### 新增欄位
返回值新增 `in_outer_rail` 標記：
```python
result = {
    "detected": bool,           # 是否偵測到強勢多單
    "side": "LONG",             # 訊號方向
    "price": float,             # 當前價格
    "kc_upper": float,          # 上軌價格
    "kc_middle": float,         # 中軌價格
    "in_outer_rail": bool,      # ✅ 新增：價格是否在外軌以上
    "reason": str,              # 詳細原因
}
```

### 判斷邏輯
- 綠K（收盤 > 開盤）
- 高點衝過上軌（`high > kc_upper`）
- 前一根K在中軌附近或以下
- ✅ 新增：檢查當前收盤是否在外軌以上

---

## 測試要點

### 場景1：黑圈轉向
```
1. 市場出現綠K從中軌衝到外軌
2. 同時持有該幣種的空單
✓ 期望：自動平倉空單，標記為進場候選（95分）
✓ 日誌：[強勢多單轉向] 訊息出現
```

### 場景2：外軌持仓持有
```
1. 多單已進場，價格在外軌以上
2. 等待出場訊號（如峰谷反轉）
✓ 期望：即使MA5反向也不出場，持續追蹤最高價格
✓ 日誌：[多單在外軌延伸中...] 訊息出現
```

### 場景3：UI 顯示
```
1. 打開圖表查看K線指標
✓ 期望：MA15 線條為深咖啡色 (#8B6F47)
✓ 對比：MA3 仍為粉紅色，MA99 仍為紫色
```

---

## 風險控制

### 保留的風控機制
- ✅ 止損（SL）仍由 `_fixed_stop_loss_loop()` 獨立執行
- ✅ 止利（TP）仍由賬戶層級管理
- ✅ 移動止利仍按既有邏輯運作
- ✅ ADX 趨勢過濾仍然有效
- ✅ 每日虧損熔斷仍然有效

### 新增的保護
- 強勢多單後平倉冷卻（記錄時間戳，避免立即反向開空）
- 外軌持仓標記（便於分析和回測）
- 峰值追蹤（供後續出場決策參考）

---

## 回滾方式

若需回滾任何改動：

### 回滾改進 #1 和 #2
編輯 `core/engine.py`，在 `_process_single_symbol()` 函數開頭移除新增的強勢多單檢測和外軌持仓邏輯。

### 回滾改進 #3
編輯 `web/index.html` 第 710 行，恢復舊顏色：
```javascript
this._ma15Series = this._chart.addLineSeries({ color: '#A67C52', lineWidth: 2, title: 'MA15' });
```

### 回滾改進 #4
編輯 `core/strategy.py` 的 `detect_strong_green_candle_burst()` 函數，移除 `in_outer_rail` 欄位。

---

## 版本信息
- **日期**: 2024-08-28
- **版本**: 2.0.1
- **修改者**: GitHub Copilot
- **相關文件**: 
  - `core/engine.py` (新增 ~80 行)
  - `core/strategy.py` (新增 2 行)
  - `web/index.html` (修改 1 行)
