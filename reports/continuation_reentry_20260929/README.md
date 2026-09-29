# 平倉冷卻兩根與延續重開

成功平倉後所有V2入口冷卻兩根，以1M開盤棒序差>=2計算，例如06:37棒內平倉，最早06:39棒重新評估。標準第三根優先，未成立時評估延續：多單最新價嚴格高於上軌與中軌、MA3>MA15、最新價>=上一根收盤；空單對稱。上一輪盈虧過濾與帳戶風控保留。

延續必須有成功CLOSE_LONG/SHORT成交紀錄，首次開倉無此資格，避免重現第二根偷跑。資格從持久化trades按成交id時間還原，最新OPEN即消耗資格；失敗平倉或單獨last_closed_at不授權延續。成交列表排序不影響結果。按使用者程式依當前方向重新評估，不限制只能原持倉方向重開。

扫描、引擎快照、提交鎖內檢查及帳戶最後驗證一致套用。冷卻診斷WAIT_POST_EXIT_2_BAR_COOLDOWN。出口不變。

180 passed in 3.03s：continuation_reentry_v2、confirmed_swing_exit_v2、strict_third_bar_v2、v2_execution_boundary、pre_entry_pnl_filter、order_sizing、startup_guard。包含實際PaperAccount重開及資格消耗，未送測試網訂單。py_compile及git diff --check成功。

截圖不能還原06:37當時逐報價與盈虧條件，未宣稱該歷史交易經回放核實，也不保證追到後续波段或追回利潤。既有AIDAN規範與指定舊測試缺失的限制不變。
