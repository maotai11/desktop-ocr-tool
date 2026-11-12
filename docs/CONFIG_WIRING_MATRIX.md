# 設定接線與節點稽核

更新版本：1.8.0-rc.17（2026-11-12）。本表描述目前核心程式的接線與限制；[v1.6.1 的 61 項基準矩陣](audit-baseline/CONFIG_WIRING_MATRIX.md) 是當時快照，不再用它的 dead／partial 結論判定現行版本。

設定被保存、UI 有欄位或測試出現鍵名，都不等於功能已接通。下表列出實際消費點與套用時機；原生 Windows 互動及封裝結果，以同一來源版本的 validation metadata 為準，不把原始碼檢查當成乾淨實機驗收。

## 現行設定

| 設定鍵 | 入口／套用時機 | 實際消費點與限制 |
| --- | --- | --- |
| `general.start_with_windows` | 一般設定；值改變後儲存，或啟動時檢查 | `set_autostart`／`app.main`；限支援的 Windows 可執行檔環境 |
| `general.start_minimized` | 一般設定；下次啟動 | `app.main` 決定浮窗是否顯示；沒有可用系統匣時仍顯示 |
| `general.single_instance` | JSON；下次啟動 | `app.main` 在取得 mutex 前讀取，關閉時允許多程序，因此不能假設其他程序的 pending 工作已失效 |
| `general.data_directory` | JSON；下次啟動 | `ConfigManager.get_data_directory` → Database／FileManager；相對路徑以 EXE 或 `DESKTOP_OCR_HOME` 為根目錄 |
| `capture.auto_ocr_on_capture` | 擷取設定；下次擷取即讀 | `Pipeline.saved` 決定一般框選截圖／全螢幕是否接續 OCR；明確的「框選辨識」始終執行 OCR |
| `capture.save_raw_image` | JSON；下次啟動 | `DbWorker` 的原圖保留／清理路徑；成功提交文字後才可清理，失敗或空白結果保留原圖 |
| `ocr.model_profile` | 模型選單；重啟套用 | `app.main` → `OcrEngine` → 已驗證模型 lock；選內含 v6-small／v6-medium，不接受舊 model 路徑代替 lock |
| `ocr.confidence_accept`、`ocr.confidence_review` | JSON；重啟套用 | `OcrEngine` 結果標籤與補辨政策；閾值不是準確率保證 |
| `ocr.enable_second_pass`、`ocr.max_ocr_passes`、`ocr.enable_handwriting_mode` | 辨識設定；重啟套用 | 啟動時建立 `OcrEngine` 參數；設定頁不跨執行緒直接 configure 正在使用的引擎 |
| `ocr.max_image_short_side` | 「小圖放大門檻」；重啟套用 | `OcrEngine` → `upscale_if_small`；鍵名沿用舊名，實際是最低放大短邊，另有 1920 短邊與 16MP 防護 |
| `clipboard.monitor_clipboard`、`clipboard.auto_save_text` | 剪貼簿設定；立即 | `ClipboardMonitoringController.refresh` 建立或卸除唯一文字監聽器；任一關閉或手動暫停即停止。新安裝監看預設關閉，重新開啟不回填當前剪貼簿 |
| `clipboard.ignore_self` | JSON；監聽政策更新時 | `ClipboardMonitoringController` → `ClipboardWatcher` 的自訂 MIME 判斷；過期監聽回呼以 generation 撤銷 |
| `clipboard.max_text_length` | JSON；每次收錄時讀 | 控制器在交付前檢查型別、長度及當下開關，超長不保存，內容不寫入診斷訊息 |
| `clipboard.deduplicate` | JSON；下次啟動 | `app.main` → `DbWorker` → Repository 去重政策 |
| `clipboard.ocr_copy_mode` | 「辨識完成後」選單；立即 | `Pipeline` OCR 完成時選擇全部複製或開啟選字視窗；晚到或過期結果不能覆蓋較新的手動複製 |
| `clipboard.copy_remove_commas`、`clipboard.copy_remove_apostrophes`、`clipboard.copy_remove_newlines`、`clipboard.copy_remove_punctuation` | 四列的啟用勾選；儲存後立即 | 名稱沿用舊鍵，現在代表該規則是否啟用；一般 OCR／歷史／編輯器經 `writer.copy_text`／`copy_text_batch` 清理輸出，`FloatingWidget.paste_last_item` 在貼上前也準備清理文字，不改原文。文件套版複製、手寫候選與貼上／還原低階路徑保持原樣 |
| `clipboard.copy_cleanup_mode_*`（commas／apostrophes／newlines／punctuation） | 各列移除／替換選單；立即 | `text_cleanup.prepare_copy_text` 讀取 remove／replace，舊設定缺鍵預設 remove；個別類別優先於所有標點，未知動作保留該類原字元 |
| `clipboard.copy_replace_*`（同上四個尾碼） | 各列自訂文字；立即 | 最多 64 個 Unicode 字元的字面字串；只在 replace 使用，不解讀表達式，插入後不再清理。`copy_batch_separator` 區分舊換行移除與明確替換的紀錄分隔；無效設定不退化成額外移除 |
| `documents.max_file_size_mb` | 文件視窗大小上限；匯入時讀 | 文件套版匯入控制器限制單檔大小，預設 100MB；另有頁數、解碼及像素資源限制 |
| `history.auto_archive_days` | 一般設定的「自動封存」；立即 | `HistoryMaintenanceController` → `DbWorker.archive_inactive` → `ItemRepository.archive_inactive_batch`；預設 90、0 停用，啟動／每日檢查最後修改時間，每批最多 200 筆 |
| `hotkeys.capture_region_ocr`、`hotkeys.capture_region_image`、`hotkeys.capture_fullscreen`、`hotkeys.toggle_widget`、`hotkeys.paste_last`、`hotkeys.open_console`、`hotkeys.quick_search` | JSON；下次啟動，設定頁唯讀顯示 | `app.main` 註冊至 `HotkeyListener` → Pipeline 的 GUI slot → 對應 action；快速搜尋開啟主控台搜尋，不在浮窗新增搜尋框 |
| `ui.widget_opacity` | JSON；建立浮窗時 | `FloatingWidget.setWindowOpacity`；實際透明度仍取決於作業系統 compositor |
| `ui.widget_position`、動態 `ui.widget_saved_x/y` | JSON／拖曳記錄；建立與移動浮窗時 | 浮窗記憶位置及螢幕內復位；動態座標不屬於未消費的孤兒鍵 |
| `ui.widget_max_items` | JSON；每次刷新清單時讀 | 浮窗近期清單顯示上限，不是資料庫總筆數或自動刪除上限 |
| `ui.widget_click_action` | JSON；點擊卡片時讀 | 浮窗卡片選取／複製行為 |
| `ui.thumbnail_size`、`ui.font_family_priority` | JSON；下次啟動 | `FileManager` 產生縮圖尺寸、`app.main` 選擇可用字體；不是設定頁字級功能 |
| `clipboard.auto_save_image`、`clipboard.auto_ocr_on_clipboard_image` | 保留為不支援設定；圖片控制停用 | 核心沒有圖片剪貼簿 → 保存／OCR 路徑；修改舊 JSON 不會啟動此功能 |
| `ui.theme`、`ui.font_size` | 保留為不支援設定；控制停用且不寫入新值 | 全介面主題／字級未完成接線，重新啟動也不視為生效 |

`config/default_settings.json` 與 Python `DEFAULT_SETTINGS` 以一致性測試核對。Python 預設值是缺檔與 merge 的來源；保存舊 JSON 額外鍵只是相容，不代表新增支援。辨識引擎參數、快捷鍵與啟動設定仍需重啟；剪貼簿監看、文字收錄、複製清理及自動封存有明確的即時套用路徑。

## 整列搜尋的會話設定

整列搜尋的模式、範圍、校正與結果只存在本次執行記憶體，不是新增 JSON 預設鍵；不把 UI 暫存狀態列成已保存設定。實際入口為 `FloatingWidget.open_shared_search`／系統匣 action → `SharedSearchWindow` → `SharedSearchController`。擷取由 `SharedCaptureWorker` 處理、OCR 共用 `OcrWorker`；`row_values` 分列取值及分配 B 位置，`search_matching` 處理文字／數字／日期候選，`RowTracker` 追蹤選取底色。

| 入口 | 實際作用 | 邊界 |
| --- | --- | --- |
| 整區 A／逐欄 A、發票 B | 整區最多 100 列、每列 20 值；逐欄最多 20 個 A，共用一個 B | 同一或高度重疊 B 文字位置只分配一次；多個確切候選需選擇，近似始終需確認；不套用複製清理 |
| 比對方式／日期換算 | 整區數字近似至少 60% 同位數，逐欄保留只差一位；文字 95%，支援的日期可換算 | 位數／格式規則仍檢查；逐字模式停用近似與日期換算，OCR 信心另判斷 |
| 上一列／下一列／選取列跟隨 | 整區切換實際 OCR 列並移動箭頭；逐欄依列距移動 A | A 整區與 B 框保持；跟隨需校正，兩次穩定後採用，不替外部軟體送鍵或捲頁 |
| 預覽／調整 | 明確點擊後暫停，取單張主螢幕快照於記憶體；接受才套用位置 | 取消／關閉清除影像，不寫檔；晚到或不同範圍的結果不能套用 |
| 標示置頂／背景透明度 | A 值與百分比依實際 B OCR 框定位，近似可用安全側邊；目前 A 列有箭頭 | 文字穿透，控制窗不置頂；操作按鈕保持可點；擷取時隱藏，無安全空間回控制窗查看 |
| 開始／手動辨識／暫停／關閉 | 明確開始才輪詢，手動強制重讀 B＋A；停止撤銷後續工作與標示 | 原生單次呼叫先返回；不寫歷史／剪貼簿／內容 Log，不在關窗後繼續跟隨 |

B 只在像素與辨識設定相同時重用一份有界記憶體 OCR 結果；同座標換發票內容仍重讀。結果發表前再驗證畫面，過期結果丟棄。沒有新增模型下載、表格結構模型或跨次啟動的配置恢復。實際格式、限制與範例見 [操作說明](SHARED_SEARCH.md)，驗證以同版摘要為準。

## 自動封存與跨視窗更新

封存依 `updated_at` 已滿設定天數判定，查看／複製不延長日期；真正編輯、標籤關聯變更或手動還原會更新時間。釘選、已刪除、已封存、OCR pending／processing、尚有未完成 OCR 工作或時間格式無效的紀錄排除。選取與更新在交易內複查條件，取消時可回滾未提交批次。

排程由 GUI 所屬控制器管理，資料操作在原有資料庫執行緒完成。同日要求合併，每批完成後才排下一批；沒有候選或失敗後不短週期重試。設定改變會撤銷舊 generation；退出會停止計時器、取消後續批次並等已接受工作回覆，才關閉資料庫。單純封存不重建未改動的全文索引。

封存不刪文字、原圖或附件，也不釋出附件磁碟空間。「已歸檔」只顯示封存資料，右鍵「取消歸檔」可還原。浮窗／主控台及編輯／標籤管理共用刷新通知，保留搜尋、分類與仍有效的選取；刪除或離開該分類的選取會清除。手動刪除是軟刪除，「清理已刪除」才永久移除，沒有自動永久刪除排程。

舊版異常中斷的 pending 紀錄沒有 PID／session／啟動時間等工作擁有者識別，無法安全判定是否仍由另一程序處理。本版保留、排除於自動封存，可手動重辨；未宣稱自動回收這類孤兒工作。

## 退出新預設的 27 個未支援鍵

以下鍵已從兩份新預設移除，完整原因以 `src/core/config.py` 的 `RETIRED_CORE_SETTINGS` 為準。ConfigManager 仍保留使用者舊 JSON 的未知鍵，不刪舊設定，也不因為舊值存在而啟用可選引擎。

| 類別 | 鍵 | 現況 |
| --- | --- | --- |
| 介面語言（1） | `general.language` | 介面沒有語系切換實作 |
| 擷取（5） | `capture.backend`、`capture.screenshot_format`、`capture.jpg_quality`、`capture.include_cursor`、`capture.capture_sound` | 核心固定 MSS／PNG，沒有這些選項的消費點；匯出格式另由明確的匯出操作決定 |
| 模型／語言（5） | `ocr.engine`、`ocr.model_det`、`ocr.model_rec`、`ocr.model_cls`、`ocr.language` | 核心改由已驗證的 RapidOCR／ONNX 模型 profile 與 lock 決定 |
| 可選引擎路由（9） | `ocr.primary_engine`、`ocr.secondary_engine`、`ocr.auto_switch_secondary`、`ocr.auto_switch_threshold`、`ocr.enable_secondary_engine`、`ocr.secondary_engine_provider`、`ocr.secondary_engine_for_handwriting`、`ocr.secondary_engine_for_low_confidence`、`ocr.secondary_engine_confidence_threshold` | Core 不 bundle 或啟用 Paddle／CnOCR；可選 adapter 的擴充 API 保留，須另備離線依賴與整合驗證 |
| 舊預處理（5） | `preprocessing.enable_deskew`、`preprocessing.enable_denoise`、`preprocessing.enable_contrast_enhance`、`preprocessing.enable_shadow_removal`、`preprocessing.binarize_method` | 舊 API 未接入現行核心；現行引擎使用自己的有界補辨流程，不藉此次掃描更換演算法 |
| 自動刪除（2） | `history.max_items`、`history.auto_delete_archived_days` | 沒有依總筆數或封存天數永久刪除的實作；與已接通的 `history.auto_archive_days` 分開 |

## 節點、旁支與驗證範圍

rc.12 的 GUI 操作與原生 PE／防毒矩陣另見 [本版驗證範圍](VALIDATION_COVERAGE.md)。下表的旁支、退休設定與舊節點記錄保留原掃描時點，不冒充重新完成所有路徑的證明。

| 掃描對象 | 本次結果／處理 | 可定位證據或限制 |
| --- | --- | --- |
| 設定 → UI → 執行流程 | 修復自動封存、剪貼簿即時開關及歷史跨視窗通知；未支援鍵依上表退休或明確停用 | `tests/test_history_maintenance.py`、`test_history_application_wiring.py`、`test_clipboard_monitoring.py`、`test_history_view_sync.py`、`test_h3_config_wiring.py` |
| A／B 多組呈現 | 一般／迷你視窗同時列各組結果，設定選單只管理組別及全文；共用單一擷取／OCR 佇列 | comparison UI／controller／group tests；rc.10 的 20 項來源／frozen／隔離探針是舊模式基準，不代表新模式覆蓋；同時可見不代表同時取樣或平行推論 |
| 舊 `AppSignals` bus | 刪除 `src/core/signals.py`，沒有現行引用；各功能仍使用實際 QObject signal | 原始碼引用掃描；不刪仍被 CLI、probe 或原生 helper 使用的入口 |
| rc.3 README 單次工作流程 | 刪除 `.github/workflows/release_readme.yml` | 已完成歷史任務；現行固定版本發布 marker 是發布保護，並非另一個未接線功能 |
| 可選 provider／舊預處理 | 保留擴充 API，退出 Core 的新預設及功能承諾 | 不因靜態引用少就載入可選引擎、重寫辨識策略或刪除擴充介面 |
| CLI、原生 helper、驗證探針 | 有主程式／建置／測試入口引用，保留 | 獨立程序及動態載入不是單靠 import 次數可判死的節點；手寫／影像 helper 的回收另見資安檢查 |
| Git 旁支與 PR | 以 rc.9 `92c2972` 為掃描起點，當時 15 個分支中的 14 個舊 head 都是 master 祖先，無獨有提交；PR #1–#12 已合併 | 此為當時遠端快照，不自動重併或刪遠端分支，不代表未來分支狀態 |
| 舊 pending OCR 工作 | 保留並排除自動封存，不自動標失敗／重跑 | 缺少可驗證工作擁有者，特別是允許多程序時，不能證明它是死工作 |

本表是針對已列設定、訊號、生命週期、入口與 Git 快照的有限稽核，不是所有動態路徑都無孤兒的形式證明。套件漏洞、網路觀察、原生呼叫取消邊界與本機未加密資料的限制見 [資安與背景工作檢查](SECURITY_REVIEW.md)。
