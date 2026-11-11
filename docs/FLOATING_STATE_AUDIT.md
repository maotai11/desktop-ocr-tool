# 浮動視窗狀態檢查（rc.14）

這次檢查從「視窗已關閉、最小化、正在框選、工作尚未返回」等狀態切入，追查延遲回覆、重新開啟及退出時的交互影響。分組檢查範圍為核對／整列搜尋、文件／歷史編輯、截圖／手寫，以及浮窗／系統匣／總退出流程；另一組交叉檢查可重現情境，補上成品 GUI 探針。

檢查只使用自行建立的文字、圖片、PDF、資料庫與視窗，沒有收錄使用者文件。下面列出實際發現與修正；這份表不是「任意 GUI 操作皆無問題」的保證。

## 實際修正

| 觸發操作 | 原本問題 | rc.14 行為與回歸證據 |
| --- | --- | --- |
| 浮窗已最小化，再由入口、截圖取消／失敗或第二次啟動喚醒；或退出中收到喚醒 | 浮窗仍最小化，或退出中的視窗重新出現 | 正常狀態還原；退出中拒絕喚醒與工具入口；`test_capture_recovers_a_previously_minimized_launcher`、`test_single_instance_wakeup_restores_minimized_but_respects_shutdown` |
| A／B 核對窗最小化，再由入口開啟 | 視窗仍停在最小化狀態 | 還原視窗，不因開窗而自動開始 OCR；`test_open_restores_minimized_panel_without_starting_comparison` |
| 整列搜尋已停止並隱藏，之後螢幕配置改變或舊錯誤才返回 | 已隱藏的控制窗自行出現 | 維持停止與隱藏；若是正在執行的精簡搜尋真的故障，仍顯示控制窗供處理；`test_inactive_hidden_search_cannot_reopen_on_failure`、`test_screen_change_recovers_active_compact_search_without_stale_marks` |
| 關閉整列搜尋後，排隊的開始操作才執行，或在啟動通知中關閉 | 搜尋在隱藏狀態重新開始，或控制窗又出現 | 尊重關閉，不重啟辨識、不重開視窗；`test_queued_start_after_close_cannot_restart_hidden_search`、`test_close_during_start_notification_cannot_reshow_controls` |
| 整列搜尋結果／狀態通知期間，立即暫停再開始 | 舊一輪清理影響新一輪擷取，或重新啟動已取消的等待 | 用每輪代號區分工作，舊流程不能清除新輪；暫停後不重啟舊 timer；`test_result_callback_restart_cannot_cancel_new_capture_generation`、`test_state_callback_cancellation_never_rearms_old_work`、`test_poll_clear_results_callback_restart_keeps_only_new_cycle` |
| A／B 核對正在準備擷取或等待時，通知內又暫停／重開 | 尚未提交的擷取留下失效代號，舊等待又排入下一次嘗試 | 清除尚未提交的擷取，不把取消工作重新排回；`test_checking_callback_restart_releases_unsubmitted_capture`、`test_waiting_callback_cancellation_cannot_rearm_retry` |
| 關閉歷史編輯器，再開同一筆；或最小化後重開 | 快取視窗仍隱藏／最小化，或沿用舊資料 | 已結束的編輯器移出快取，重開讀取最新資料；仍在編輯的最小化視窗還原並保留未儲存草稿；`test_finished_history_editor_reopens_with_current_saved_text`、`test_minimized_history_editor_restores_without_discarding_draft` |
| 文件框選拖曳途中隱藏、進入忙碌、縮放或切換平移 | 恢復後放開滑鼠，仍提交上次手勢座標 | 取消未完成手勢，不建立舊範圍；下一次新拖曳正常框選；`test_interrupted_document_drag_does_not_commit_old_coordinates` |
| 截圖拖曳途中按 Esc、隱藏、關閉或直接重新框選 | 再次放開滑鼠使用上次起點，誤完成新框 | 重新框選必須先有新的按下動作；`test_interrupted_drag_requires_fresh_press_after_restart` |
| 截圖剛選好、延遲交付尚未發生時關閉；或結果回呼內立即再框選 | 已取消結果仍交付，取消通知重複，或舊回呼影響新框選 | 關閉／取消退役該次交付；回呼只交付一次，交付前先移除舊回呼；`test_close_or_cancel_retires_pending_capture_callback`、`test_delivered_callback_is_consumed_before_reentrant_capture` |
| 手寫停筆後立即隱藏／關閉，或關閉時工作者同步回報已停止 | 延遲辨識仍送出、筆畫手勢殘留，或關閉中又恢復辨識 | 隱藏先停用、停止延遲送出與介面 timer、取消未完成手勢及該次工作；只有明確重新開啟才恢復；`tests/test_handwriting_window_lifecycle.py` |
| 手寫測試 helper 報錯的回呼內立即重試，舊 helper 尚未回收 | 新重試被舊 helper 的結束通知丟棄 | 等舊 helper 回收後再處理待重試工作；若此時停止，重試一併取消；`test_retry_from_failure_waits_for_old_helper_reap`、`test_stop_in_failure_handler_cancels_a_reentrant_retry` |
| 一般截圖與 A／B／整列搜尋框選相互切換 | 不同入口各留一個框選，或舊取消回覆把浮窗重新叫出 | Pipeline 統一管理入口，切換先取消前一個；退出時取消現有框選且不復活浮窗；`test_normal_and_comparison_selectors_are_mutually_exclusive`、`test_shutdown_retires_normal_selector_without_reveal_or_late_submission` |
| 設定父視窗已取消／退出，標籤或顏色子對話仍在等待，之後返回確認 | 已結束的設定仍新增／修改／刪除標籤、寫設定或開機項目 | 父視窗先進入終態並關閉子對話，晚到確認不能變更完成結果或寫入；`test_nested_tag_accept_after_settings_rejected_does_not_write`、`test_finished_settings_rejects_queued_save_and_accept`、`test_shutdown_unwinds_both_tag_and_color_modal_loops_without_saving` |
| 總退出已開始，系統匣手寫或設定完成事件才到達 | 已停用浮窗仍建立新工具，或套用舊設定 | 工具入口及模態返回檢查退出狀態，不新建工作、不儲存；`test_tray_cannot_create_handwriting_after_shutdown_begins`、`test_modal_return_after_shutdown_cannot_publish_settings` |
| 程式退出期間，歷史 DB 回覆、刪除／清理／匯出／另存對話才返回 | 獨立視窗仍能新增工作，或再次彈出完成訊息 | 歷史與編輯器進入停止狀態，保留記憶體草稿，不再存檔、改 DB、匯出或排 OCR；`test_history_shutdown_ignores_queued_db_responses_and_keeps_editor_draft`、`test_history_modal_return_after_shutdown_cannot_write_or_export`、`test_editor_shutdown_blocks_rerun_and_file_dialog_completion` |

回歸測試位於 repository 的 `tests/test_comparison_ui.py`、`test_shared_search_ui.py`、`test_comparison_controller.py`、`test_shared_search_controller.py`、`test_document_history_lifecycle.py`、`test_capture_overlay_lifecycle.py`、`test_handwriting_window_lifecycle.py` 、`test_handwriting.py`、`test_settings_lifecycle.py` 及 `test_floating_shutdown_entries.py`。這些是可重現的事件順序，沒有把同一個 assert 重複計算成不同功能的完成證明。

手寫另有實際測試子程序的回收回歸：`test_hidden_window_reaps_hung_helper_and_explicit_reopen_recognition_succeeds` 啟動會掛住的自有 helper，隱藏視窗後確認程序、工作與等待 timer 都退役，隱藏期間不接受新辨識；明確重開並送出新的辨識後才成功。這是程序生命週期證據，不是筆跡模型準確率測試。

## 成品要再次檢查的操作

既有 GUI 探針保留設定、歷史、編輯、選字複製、文件、手寫、兩種核對與退出流程。rc.14 再增加三個具名 check：

| Check | 要驗證的狀態轉換 |
| --- | --- |
| `minimized_windows_restore` | 最小化工具再開啟後可見，不能仍停在最小化狀態 |
| `capture_cancel_restart_ignores_old_gesture` | 中斷框選後重新開始，舊滑鼠放開事件不能完成新框 |
| `shutdown_rejects_late_tool_entries` | 總退出開始後，晚到的工具入口不能再建立／啟動工作 |

因此同版 GUI 報告必須包含 **15 項**檢查，在來源、打包 EXE、防火牆及隔離 profile 四種模式分別執行。具體操作與檢查的副作用見 [驗證矩陣](VALIDATION_COVERAGE.md)。手寫項目另測隱藏後超過送出延遲，仍不新增辨識請求，直到明確重新開啟；這個使用替身的介面檢查不能取代原生 helper 的程序回收測試。

第二次啟動的喚醒分支另用合成 Windows 訊息緩衝區測試正常還原與退出拒絕；這個來源回歸不等於已跑過所有 Windows 程序間通訊情境，也不由三項新 GUI check 代證。

新回歸測試與成品 GUI 探針分開判讀。pytest 通過只證明列出的狀態與事件順序；四種 GUI 結果仍須各自出現在同版 `validation-metadata.zip`，對上來源 commit 與 EXE SHA256。本地 offscreen 通過不能代替 Windows 原生桌面結果，也不能沿用 rc.13 報告。

## 保留的行為與限制

- 開窗不等於開始 OCR。需要持續核對的功能，仍由使用者按開始；暫停／關閉後不再排下一輪。
- 退出會阻止新增工作並丟棄過期回覆；已進入單次原生辨識的工作可能要等它返回才能收尾，不能保證任意原生運算立即中斷。
- 這次狀態修正不新增網路連線、執行資料檔或新的常駐輪詢。測試自己的資料與外部白底視窗由測試回收。
- 這些有限情境不涵蓋任意外部軟體、所有滑鼠／觸控筆順序、混合 DPI 多螢幕、遠端桌面、休眠恢復或任意文件辨識準確率。原生元件與防毒的範圍也不由 GUI 測試代證。
