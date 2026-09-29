# 嚴格第三根開倉

原 CONTINUATION 單根站外路徑可以在第二根收線時直接產生訊號。本次改為唯一 THIRD_BAR_CONFIRMED_LONG/SHORT；舊 TRACK_RIDING、INTRA、THREE_BAR_BREAKOUT、CONTINUATION 訊號不再授權送單。

前兩根必須明確已收線，三根時間戳連續相差 60000ms。第一根與第二根同色且各自收盤嚴格在同側外軌之外；第三根用最新價，允許同色、反色與十字，但反向實體不得超過第二根實體50%。正好50%允許。按使用者最新提供的程式，不另要求第一根開盤在軌內，也移除原第一根0.5 ATR實體門檻。保留上一輪盈虧過濾。

當前第三根失效時不回頭使用上一根收線訊號；掃描、引擎、帳戶共用同一判定。runner直接傳遞實際第三根時間戳，不再依舊類型名稱推測確認根。

驗證：120 passed in 2.48s。包含 strict_third_bar_v2、v2_execution_boundary、pre_entry_pnl_filter、order_sizing、startup_guard。py_compile與git diff --check成功。測試未發送外部訂單。

出口尚未修改：使用者標題要求唯有真峰谷，但未提供具體出口段落；已詢問是否一般出口需左右各一根確認的峰谷收線破位、無峰谷續抱且保留緊急與硬止損，等待答覆。已發現原出口無峰谷時放行，且出口3繞過峰谷，尚未將這些問題宣稱為已修復。
