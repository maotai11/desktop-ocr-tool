# 修復後設定接線差異

完整逐key基準矩陣保留於 [61 leaf baseline matrix](audit-baseline/CONFIG_WIRING_MATRIX.md)。本表只列此次變化；未列項目仍採基準的dead/partial狀態，沒有因保存設定就視為支援。

| Config key | UI | Runtime consumer | 證據／Packaging |
|---|---|---|---|
| general.single_instance | JSON | app.main在acquire mutex前讀取 | source；完整Windowsapp smoke gate |
| general.start_minimized | checkbox | app.main決定widget.show；無tray必show | source；原生tray互動仍待驗收 |
| general.data_directory | JSON | ConfigManager→Database/FileManager | source與tempDBtests；相對EXE或DESKTOP_OCR_HOME |
| capture.save_raw_image | JSON | DbWorker._save_raw_image，commit後unlink | failure injection；失敗/empty保留 |
| capture.auto_ocr_on_capture | checkbox | Pipeline.saved適用region_image/fullscreen；明確region_ocr始終OCR | source；native擷取gate未完成 |
| ocr.max_image_short_side | spinbox標為小圖放大門檻 | app→OcrEngine→upscale_if_small | key名仍legacy，語意實為minimum-upscale threshold；16MP拒絕guard |
| ocr.enable_second_pass / enable_handwriting_mode | checkbox | 啟動時OcrEngine參數 | 重啟套用，不再GUI直接configure |
| ocr.confidence_accept / confidence_review | JSON | OcrEngine結果標籤/retry | 現有unit＋pipeline；不代表完整Detection Recall |
| ocr.primary_engine / secondary_engine / auto_switch_secondary / auto_switch_threshold | 移除誤導controls | Core不使用；legacy設定保留不執行 | 原log-only及conflict不可宣稱修成多enginefeature |
| ocr.enable_secondary_engine / secondary_engine_provider / secondary_engine_for_* / secondary_engine_confidence_threshold | 移除 | Core不使用；實驗adapter API仍有參數 | Core不bundle Paddle/CnOCR；nativeintegration gate未通過 |
| ocr.model_det / model_rec / model_cls | 無切換UI | 實際由verified models.lock.json唯一決定 | 舊key仍legacy；bundle載入核對modelSHA |
| clipboard.monitor_clipboard | checkbox | app建立watcher，fresh default=false | config/restarttest；舊profile保留原選擇 |
| clipboard.auto_save_text | checkbox | app text_captured→DTO→Pipeline.save_requested | 仍可能保存敏感內容；沒有retention |
| clipboard.ignore_self | JSON | ClipboardWatcher custom MIME判斷 | source；Windowsclipboard全流程未驗收 |
| clipboard.copy_remove_commas / copy_remove_apostrophes / copy_remove_newlines | 三個獨立checkbox，全部預設false | copy_text／copy_text_batch／paste_last 的輸出邊界 | 儲存立即生效；字元清理不改OCR／人工文字／匯出，換行合併警告；批量保留筆間分隔 |
| clipboard.auto_save_image / auto_ocr_on_clipboard_image | 圖片checkbox停用 | 未完成 | 不以UI承諾此功能 |
| ui.widget_opacity | JSON | FloatingWidget.setWindowOpacity | source；compositor視覺效果Windows待驗收 |
| ui.theme / font_size | controls停用 | 未完成 | 不再宣稱重新啟動即生效 |
| hotkeys.quick_search | JSON | HotkeyListener→Pipeline slot→widget.focus_search | 原生global hotkey互動gate待驗收 |
| history.max_items / auto_archive_days / auto_delete_archived_days | 無完整UI | 未完成 | 文件明示無自動retention，不默默開始刪舊資料 |
| preprocessing.* | legacy JSON | 未接到目前OcrEngine pipeline | 原有dead設定保留，enhance仍是固定流程 |

`config/default_settings.json` 與 Python DEFAULT_SETTINGS有exact equality test。複製清理儲存後立即生效；引擎／監聽等啟動設定仍需重啟。被停用的controls不再寫入新值。仍有legacy/dead keys，未將此表包裝為完整設定清理已完成。
