"""Render the completed offline body-risk audit without refitting or selecting."""
import json
from pathlib import Path

ROOT = Path('reports/channel_entry_risk_current')


def write_report(root=ROOT):
    r = json.loads((root/'adverse_body_results.json').read_text())
    pct = lambda x: '—' if x is None else f'{100*x:.1f}%'
    rows = ['| 時段／幣種／方向 | 候選 | 異常率：原始 → 保留 | 擋下異常 | 誤擋非異常 | 錯過正報酬代理 |',
            '|---|---:|---:|---:|---:|---:|']
    groups = [('舊資料驗證', r['selected_test'])] + [
        (f"近期 {x['symbol']} {x['side']}",x) for x in r['recent']]
    for name,x in groups:
        rows.append(f"| {name} | {x['n']} | {pct(x['base_bad_rate'])} → {pct(x['kept_bad_rate'])} | {x['bad_blocked']}/{x['bad']} | {x['nonbad_blocked']} ({pct(x['nonbad_miss_rate'])}) | {x['profitable_proxy_blocked']}/{x['profitable_proxy_count']} ({pct(x['profitable_proxy_miss_rate'])}) |")
    comparisons = []
    for x in r['results']:
        if x['symbol'] == 'ALL':
            comparisons.append(f"- {x['filter']}：保留後異常率 {pct(x['kept_bad_rate'])}，攔截 {x['blocked']} 個，其中異常 {x['bad_blocked']} 個；錯過正報酬代理 {pct(x['profitable_proxy_miss_rate'])}。")
    text = '''# 反向長 K 事前風險研究續驗

結論：目前證據不足，不新增自動開倉攔截。模型在舊資料有小幅辨識力，但近期 PEPE 多空均未改善，不能據此修改實際交易策略。此輪只更新離線研究、測試與報告，沒有重啟服務。

## 資料與驗證設計

兩幣各 43,400 根舊 1 分鐘 K 線；另各有 2,191 根先前已補抓的 K 線，期間為 2026-09-08 21:40 至 2026-09-10 10:11 UTC（結束不含），約 36.5 小時。本輪沿用這批補抓資料，沒有宣稱重新下載至現在。舊資料研究區間與時間切點見 README.md。

讀取器已驗證分鐘時間連續、無重複、OHLC 有效、成交量非負及收線時間；新舊邊界連續，近期最後一根在下載時已收線。新檔記錄 Binance `/fapi/v1/klines` 來源與下載時間，來源及 SHA-256 見 adverse_body_results.json；舊檔雜湊見 results.json。這是本地完整性檢查，沒有逐根重新向交易所核對。行情欄位可對照 [Binance 官方 USD-M connector](https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/market.py)。

使用現行共用 aligned_entry 產生下一分鐘開盤的候選。僅用先前已收線資料及已知開盤價計算特徵，並重算開盤時的 MA／CK。近期資料銜接完整舊歷史，避免截短 EMA 暖機歷史造成偏差。

本次主標籤明確改為：下一分鐘反向收盤實體至少前根 ATR 的 0.5 倍；空單為長綠實體，多單為長紅實體。盤中反向觸及 0.5 ATR 是另外一個標籤，不能混稱收盤長實體，也不能把未收成長實體的一律稱為純影線。

模型與門檻只在原先前 60% 訓練／中間 20% 調整；後 20% 與近期資料均不再調整門檻。這是新增標籤的探索性分析，舊資料已被研究過，不能冒稱全新盲測。近期資料是在舊期間之後的時間驗證，亦非未來前瞻影子驗證。

## 反向收盤長實體結果

''' + '\n'.join(rows) + f'''

舊資料驗證：擋掉 227/748（30.3%）候選，異常率降低約 2.6 個百分點，但錯過 39/136 個正報酬代理機會。近期擋掉 51/174（29.3%）候選，異常率僅下降約 1.6 個百分點。近期涵蓋 3 個 UTC 日期且首尾是不完整日；按日 bootstrap 的風險差 95% 區間約 -3.8 至 +4.0 個百分點，包含沒有改善，日期太少也使區間不穩定。

近期 174 個候選有 79 個盤中反向達門檻，其中只有 41 個最後收成反向長實體。兩種事件不能共用同一組命中數。

「最多擋 25%」僅是調整段選門檻的約束；門檻固定後，驗證段可能超過 25%，本次確實發生。不能宣稱實盤攔截比例有硬上限。

## 哪些事前特徵有證據

''' + '\n'.join(comparisons) + f'''

上述皆為舊資料最後驗證段，使用各自在調整段選出的門檻。拒絕影線單獨使用時，調整段沒有正效益，因此選擇不攔截；單純軌外距離只把異常率由 24.5% 降至 24.2%，代價偏高。

綜合模型納入實體／影線、1／3／6 根推進、MA3／MA15 斜率、量能、ATR 相對波動及開盤缺口等事前特徵。部分權重與較長拒絕影線、較弱 MA15 斜率有關，但權重只是訓練相關性，不是單一特徵有效性的證明。3 根報酬和 MA3 斜率存在代數重複，不能把兩者視為兩份獨立證據；本輪保留既定特徵組，沒有看完驗證再換模型。

收盤長實體模型 AUC={r['test_logistic_auc']:.4f}；Brier={r['test_logistic_brier']:.6f}，按幣種／方向基準為 {r['test_stratum_baseline_brier']:.6f}，改善很小。沒有找到可跨兩幣穩定採用的單一攔截條件。

## 正常機會與交易效益的界線

非異常不等於盈利。本報告另列「開盤進、同分鐘收盤出、扣雙邊手續費與滑點後為正」的機會損失，它只是代理，沒有模擬現行出口。近期代理平均報酬從 -15.74 改到 -12.28 bps，仍為負；這也不能當作現行策略收益或損益改善。

1m OHLCV 無法重建同根先回調 0.10 ATR、再轉向 0.05 ATR 的報價順序。候選並非已滿足完整送單條件的成交，未模擬回踩票據、持倉互斥、每根限次、末端空間與帳戶風控，也沒有保證實際出口前仍會遭遇標記的風險。真正會錯過多少現行策略獲利單，目前資料無法回答。

因此本輪不部署攔截、不改出口。若繼續驗證實際成交效益，應使用送單前狀態及逐筆報價做未來時段影子驗證；不能拿本次驗證結果反覆調門檻後仍称為保留段。此輪未啟動背景收集。

## 重現與檢查

```bash
OPENBLAS_NUM_THREADS=1 .venv/bin/python3 tools/channel_entry_risk_research.py --output reports/channel_entry_risk_current
OPENBLAS_NUM_THREADS=1 .venv/bin/python3 tools/channel_adverse_body_audit.py
.venv/bin/python3 tools/channel_entry_risk_report.py
OPENBLAS_NUM_THREADS=1 .venv/bin/python3 -m pytest -q tests/test_channel_entry_risk_research.py
```

本輪研究測試 4 項通過：當根／未來 OHLCV 不影響事前特徵、開盤指標與因果重算一致、時間切分排除跨界標籤、異常與正報酬代理的誤攔分母分開。沒有修改交易行為，未重跑交易全套；既有出口測試失敗仍以 scratch/current-exit-validation.md 為準。

adverse_body_results.json 保存凍結模型、參數、門檻、各組統計及近期資料雜湊；body_holdout_predictions.csv 與 recent_body_predictions.csv 保存逐筆結果。
'''
    (root/'ADVERSE_BODY_REVIEW.md').write_text(text)


if __name__ == '__main__':
    write_report()
