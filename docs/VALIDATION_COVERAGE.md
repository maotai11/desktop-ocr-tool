# 版本驗證範圍與結果判讀

目前版本：1.8.0-rc.22。下列探針在建置／驗證時執行，不是一般使用時的背景掃描。這份文件將 GUI 操作、OCR 功能、原生元件與防毒分開列出；「指定檢查通過」只指表內的資料、操作及環境，不能解讀為任意 GUI 輸入或所有 DLL 都安全。

每版實際結果以該版 Release 的 `validation-metadata.zip` 為準，必須同時對上來源 commit 與 EXE SHA256。缺少、跳過、取消、逾時、不支援或未完成的必需報告不能當成通過；不能拿前版的綠燈替代新版。這份操作範圍表不是預先宣告尚未執行的發布檢查已成功。

## 版本之間的差別

| 版本 | 已有證據 | 不能從該版推論 |
| --- | --- | --- |
| rc.10 | 預設 90 天封存、設定接線與固定位置 A／B 多組顯示的測試；下載後另核對官方 EXE 與 91 個 `src` 模組來源相符 | 不是新整列搜尋、全 GUI 或全部原生 DLL 的覆蓋；下載檢查的完整防毒掃描未完成 |
| [rc.11](https://github.com/maotai11/desktop-ocr-tool/releases/tag/v1.8.0-rc.11) | `4756c331b29092035698fa84f134c3b9589303a3` 的公開摘要中，Windows bounded tests 為 1,881 通過／4 略過，Linux 為 1,871 通過／4 略過；整列搜尋 16 項生命週期檢查、固定位置核對及其他來源探針通過，Windows 同版 EXE 的 frozen、阻擋對外連線及隔離 profile 探針通過 | `clean_machine_verified=false`，整列搜尋生命週期探針也明列未測模型推論／準確率；沒有本版新增的面板／操作矩陣、全 PE 清冊或完整 Defender 結論。略過項目不能算成通過 |
| rc.12 | 在既有功能驗證之外，新增下列 GUI、B 區標示與原生元件／防毒檢查；各項是否完成，須讀同版結果 | 不延用 rc.11 成功或 Qt 升級需求，冒充 rc.12 EXE、Defender 或未知漏洞已驗證 |
| rc.13 | 改為 A 框下方文字，加入開始收起／恢復介面操作，以及切回來源與重複擷取後的原生置頂檢查 | 實際通過狀態依同版成品報告，不沿用 rc.12 報告 |
| rc.14 | 修正浮窗恢復、取消／重開手勢、通知重入及退出後晚到工作；GUI 探針新增三項狀態轉換，詳見 [浮窗狀態檢查](FLOATING_STATE_AUDIT.md) | 回歸測試不等於任意操作或所有平台皆已覆蓋；四種成品模式仍讀同版結果 |
| rc.15 | 整列搜尋改為選定 B OCR 文字下方的數值／百分比；新增數字近似、日期換算、手動重辨與背景透明度，整列搜尋具名檢查共 22 項 | 合成匹配與視窗檢查不證明模型準確率、任意日期格式或實體乾淨電腦驗收；四種模式仍讀同版結果 |
| rc.16 | 整區多列取值、B 位置排他、近似候選可見、目前列箭頭、同位置換發票與預覽調整；shared 27、GUI 16 項 | 不把行級 OCR 框當字元／儲存格，不宣稱任意版型、數字或純文字自動分欄皆可正確辨識 |
| rc.17 | 設定分區及四種移除／替換規則；既有 GUI 16 項加入取消、持久化、動作切換及文件套版複製不受清理影響 | 不以合成設定字串推論任意剪貼簿內容、所有原生對話或全部設定排列已覆蓋 |
| rc.18 | 重整主浮窗並加入複製模式／四項清理快捷入口；移除 A／B、整列搜尋及專用預覽；GUI 15 項包含快捷操作與舊功能不存在契約，網路 6 組，metadata 33 份 | 刪除功能與調整證據清單不等於新版已通過；仍須取得同版完整測試、EXE、安全及隔離結果 |
| rc.19 | 新增手動發票核對與人工影像候選；GUI16項含Windows200%核對按鈕保留來源焦點；新增雙階段真模型／helper資源報告，metadata34份 | 不以模型大小代替RAM，不以合成匹配代替任意發票辨識準確率；仍須同版完整EXE報告 |
| rc.20 | 發票核對驗證面板恢復、背景透明度選擇與 Windows 原生鍵盤操作，沿用 16 項 GUI 檢查與 34 份發布報告 | Windows 成品、資源及隔離驗證以 rc.20 同次報告為準 |
| rc.21 | 離線自測以合成雙碼發票讀取中文品名、數量與單價，驗證封裝的 QR 解碼及解析 | 實際收錄的品項取決於商家，螢幕縮放會影響條碼可讀性 |
| rc.22 | OpenCV 搭配離線 ZXing-C++ 3.1.1 解碼。以合成發票檢查輕度模糊及縮小後的品項讀取，逐欄比對品名、數量、單價 | 原圖缺少可解碼的方格細節時，仍需較清楚的來源 |

## GUI 要驗證到哪裡

GUI 探針使用測試自己建立的文字、圖片、PDF、資料庫及視窗，實際開啟 Qt 元件、觸發指定控制與檢查狀態變化。原生 OCR、滑鼠／作業系統互動及檔案內容有各自的探針；合成操作不代表所有真人操作、任意文件或所有外部軟體都已覆蓋。

本版 GUI 探針有下列 **16 個具名檢查**，不是「任意操作都測過」。發布須分別核對同版 `source-gui.json`、`frozen-gui.json`、防火牆及隔離 profile 的 GUI 結果；本地 offscreen 通過不代替 Windows 成品證據。

| 報告 check | 實際操作／副作用 | 本版結果依據 |
| --- | --- | --- |
| `launcher_actions_and_capture_cancel` | 點浮窗 OCR／截圖，進入真框選工具後 Esc 取消；開檔案位置選單 | 同版 GUI 報告 |
| `launcher_copy_controls` | 真浮窗切換全部／選字並重讀保存值，同模式不重複寫；清理面板取消維持設定 bytes／剪貼簿，替換／移除／停用後檢查複製效果，不送完整設定通知或修改其他值 | 同版 GUI 報告 |
| `settings_cancel_and_apply` | 真 modal 編輯四列後取消不寫；儲存替換、重開、切移除／停用並檢查剪貼簿；通知剪貼簿／封存控制器，開機啟動 OS 寫入用替身 | 同版 GUI 報告 |
| `history_search_tags_and_pin` | DB worker 存兩筆、歷史搜尋、右鍵釘選同步浮窗；新增標籤並套用 | 同版 GUI 報告 |
| `editor_save_and_history_refresh` | 編輯器保存文字／備註，兩個歷史視窗同步刷新 | 同版 GUI 報告 |
| `ocr_selection_copy_explicit` | 真保存 → 可控 OCR 回覆 → DB → 選字窗；未選不複製、清除選取、只複製第二行 | 同版 GUI 報告 |
| `document_import_template_zoom_clear` | 真 PNG／PDF 匯入與 renderer；縮放／拖曳／滑鼠框選；可控辨識後填模板、清理設定開啟時複製及預覽仍保持模板原文、清框／清文件 | 同版 GUI 報告 |
| `handwriting_stroke_candidate_copy` | 真 pad 滑鼠筆畫 → 可控候選 → 點字複製；續寫座標不變、撤銷／清除；隱藏超過 debounce 不再送出，明確重新開啟才恢復 | 同版 GUI 報告 |
| `removed_comparison_tools_absent` | 真浮窗／系統匣沒有退休入口、專用視窗／工作者／模組不存在，舊 CLI 參數實際被拒絕 | 同版 GUI 報告 |
| `manual_invoice_check` | 真核對視窗／控制器、合成擷取與可控OCR驗證目前列、五欄、標示、取消、不存歷史或剪貼簿；Windows另跑200%原生點擊及來源失焦負對照 | 同版GUI報告及原生required/verified兩個真布林 |
| `tray_pause_and_quit_route` | 系統匣／浮窗的暫停同步；退出走 Pipeline 關閉 | 同版 GUI 報告 |
| `minimized_windows_restore` | 最小化後重新開啟工具／編輯窗，檢查已還原 | 同版 GUI 報告 |
| `capture_cancel_restart_ignores_old_gesture` | 真框選拖曳取消／重開，舊 release 不產生擷取；一般 OCR／截圖使用實際 Pipeline 入口 | 同版 GUI 報告 |
| `shutdown_rejects_late_tool_entries` | 總退出後觸發工具入口，檢查不建立新工作與視窗 | 同版 GUI 報告 |
| `windows_and_workers_closed` | 本次視窗、OCR／DB／文件／手寫工作者完成關閉 | 同版 GUI 報告 |
| `temporary_files_cleaned` | 刪除探針自己建立的暫存，不動既有 profile | 同版 GUI 報告 |

此 GUI 探針明列 `model_inference_tested=false`、`ocr_accuracy_tested=false`、`clean_machine_verified=false`。原生檔案挑選器只回傳測試自己建立的檔案；自動啟動的 OS 寫入使用替身，未在這個探針修改開機項目。真 OCR 模型、原生手寫 helper、真桌面擷取結果、任意 GUI 狀態、實體滑鼠及多 DPI 不由這 16 項代證，須讀各自功能探針或人工驗收。

rc.18 不再執行固定位置核對、整列搜尋或專用範圍預覽的功能探針，也不接受舊報告冒充本版證據。現行 GUI 的移除確認檢查應在 source、frozen、防火牆及隔離 profile 四種模式分別通過；有殘留入口、模組或工作者即不能通過這個檢查。

發布 metadata 契約為 34 份（新增實際資源報告）；其中 candidate probe 由 6 份改為 5 份，隔離 profile probe 由 8 份改為 6 份。Linux 網路觀察保留六組現行合成路徑。這些數量只描述驗證範圍，實際通過數、略過項目與所有雜湊均讀同版報告。

## 原生元件不是一個「無毒」勾選

| 層次 | 能回答的問題 | 不能代替的證明 |
| --- | --- | --- |
| Windows runtime bootstrap | 鎖定 Astral archive SHA256／PE 清單；隔離 smoke 的 Python／OpenSSL 版本、`_ssl`／`_hashlib` 來源及 pip 可用 | 不是全部內嵌元件 CVE 審查，也不能代替 frozen 功能或 Defender 完成 |
| Python 安裝清單＋pip-audit | 實際 distribution 及當時 Python 公告資料庫的已知命中 | Qt、PDFium 等原生公告或靜態嵌入元件的完整 CVE 掃描 |
| 實際成品 PE 清冊 | 外層 EXE 加所有封裝 PE（含 DLL／PYD／helper），逐檔雜湊與來源可對應，並重新核對 CArchive 實際內容 | 每個 PE 內部所有靜態庫的完整 SBOM，或整台電腦的檔案清查 |
| PE 匯出表完整性 | 上限 131,072 個符號，核對原始表範圍／數目與解析結果，拒絕截斷 | 不是無限大小 PE 的支援，也不是 DLL 程式碼行為驗證 |
| PE 匯入檢查 | regular／delay imports 能解析至 bundled、System32、實際 API-set 或候選 manifest 選定的 `windows_sxs`；缺符號必須為零 | 任意動態載入、所有執行路徑，或引用系統 DLL 的完整安全性 |
| 簽章檢查 | 每個 PE 必須得到 `Valid` 或明確的 `NotSigned`；來源為 Windows 系統檔時要求有效簽章 | `NotSigned` 不是有效簽章，也不是惡意結論；其他未知／不可用／無效狀態不能冒充通過，有簽章也不代表無漏洞 |
| Qt 原生公告核對 | 實際模組及版本是否落入已查到的受影響區間 | 不把未封裝模組的公告算成產品漏洞，也不推論未知公告不存在 |
| Defender 外層 EXE＋解出的全部 PE | 記錄引擎／病毒碼／時間與掃描範圍雜湊；檔數必須等於 PE 清冊，掃描完成且無偵測 | 不代表所有非 PE 資源都已獨立解包掃描，也不是永久無毒保證；不可用、未掃描、逾時或資源限制不能當零偵測通過 |

rc.12 將 PySide6-Essentials／shiboken6 鎖定至 6.11.3。舊 OpenSSL 3.0.16 的來源經 SHA256 核對為 CPython 3.12.10；舊包另有檔名帶 `-x64` 的 OpenSSL 3.5.9，是 Qt 打包時外帶的另一份依賴。Windows 改採鎖定的 Astral python-build-standalone CPython 3.12.16／20261026 發行包及其動態 OpenSSL 3.5.9，Linux 開發／測試維持 Python 3.12；RapidOCR 1.4.4 要求 Python < 3.13。

Astral 是該 Windows 發行包供應者，並非 python.org 官方安裝器；建置 bootstrap 核對整包 SHA256 與 PE 清單，來源雜湊詳見 [資安檢查](SECURITY_REVIEW.md)。這不代表新 runtime 已通過 Windows 執行／Defender，仍須由同版成品報告確認。Qt TLS plugins 與外帶 OpenSSL 另行排除，不靠移除 Python 必要 SSL 依賴代替 runtime 修補，也不修改使用者系統。Qt5Compat 是否封裝以清冊為準，不能由套件名稱推論；版本核對仍是有限範圍，未完成所有內嵌元件 CVE／SBOM 審查。

### 相容 stub 只在有系統替代證據時排除

舊封裝中的 43 個 API 相容 stub 加 `ucrtbase.dll`，供應來源仍未確認。最終 Analysis 只在 Windows 10+ 以實際 namespace／System32 證明保留檔案對這份候選清單的顯式匯入及有界 forwarder 鏈（含保留 bundled DLL 的相關轉送）都可滿足時，才不帶入這份明確清單。不能證明則保留，後續嚴格來源 gate 不因此放行未知來源；一般 `msvcp`／`vcruntime`／SSL 依賴保留。

直接保留匯入與 bundled forwarder 使用真實匯入者 basename；OS forwarder 的下一步使用實際 OS 父模組。沒有已觀察到請求者的 API 候選，須有唯一有效預設，並證明 schema 的所有有限 host 選擇都為可用 AMD64 系統 DLL；缺失／衝突時保留。這是排除政策的全選項檢查，不是替未知匯入者猜一個分歧 host，也不等於已執行那些 DLL。

排除不是核准這 44 個舊檔的來源，也不是刪除系統檔。匯出表上限由 pefile 預設 8,192 改為 131,072，且必須通過原始表範圍與解析數目一致性檢查；超界或截斷直接拒絕。SxS 中繼資料只證明實際 manifest 選定的 Common Controls 6 目標，不代表掃描整個 WinSxS、任意 SxS 元件或所有執行路徑。新增規則的 Windows／Defender 完成狀態仍以同版報告為準。

### 哪些報告與欄位必須對得上

`artifacts/native-payload-inventory.json` 逐檔列出相對路徑、SHA256、AMD64 PE 身分、檔案／元件版本、來源類別、匯入與簽章狀態。來源限定可核對的 wheel RECORD、鎖定的 Astral CPython runtime、鎖定的原生 helper、PyInstaller bootloader 程式區段或 Windows 系統檔；不把任意 PATH 中同名 DLL 當成可信來源。System32／API-set 解析使用實際 Windows 的 apisetschema v6 hash／修訂結構、KnownDLLs 及完整檔案 exports；條件 host 以真實匯入者 basename 不分大小寫完整匹配 alias，否則只採唯一有效預設；匯入者未知且 host 分歧，或當下所需的 alias／預設缺失或衝突時不猜測，不能混成一份可用列表。Common Controls 6 依候選 EXE manifest 的 activation context 選定 WinSxS 中繼資料，`windows_sxs` 目標須為受限相對路徑、SHA256 相符且為 AMD64。此過程不載入待檢查元件；package scope 中能找到 DLL，也不代表它或所有匯入分支已實際載入執行。

`artifacts/native-payload-audit.json` 必須與清冊、EXE、來源 commit 及同次工作流程一致，且：

- `native_security_policy_passed=true`，`native_security_policy_sha256` 對應同版版本政策原始檔；不能換了政策仍沿用舊報告。
- `unmapped_count`、`unresolved_dependencies_count`、`invalid_signatures_count`、`antivirus_detections_count` 都是 0。
- `signature_files_checked` 與 `antivirus_scanned_files_count` 等於清冊的 `native_files_count`；清冊也必須涵蓋實際 CArchive 全部 PE，不能只掃已列出的部分。
- `antivirus_scan_completed=true`，掃描器確為 Microsoft Defender Antivirus，病毒碼在掃描開始時不超過 72 小時；逾時或只有掃描啟動紀錄不算完成。
- 掃描範圍是外層 EXE 及解出的全部 PE，掃描前後檔案雜湊一致；不是系統全磁碟掃描。

掃描器只讀取既有 Defender 設定，要求本機掃描、不提交樣本，不加排除或改防護設定；設定不符或掃描器不可用時失敗，不能產生零偵測通過。

病毒碼超過 72 小時時，建置驗證才會嘗試官方 `MpCmdRun.exe -SignatureUpdate -MMPC`，最長 180 秒。更新前先確認服務、隱私設定及工具的 Microsoft 有效簽章，更新後重新檢查原本 72 小時門檻；更新失敗、逾時或仍過期不通過。這只更新建置機病毒碼，不改防護設定，不在應用程式執行期間更新或建立背景工作；更新輸出限 runner 暫存並於結束清理，實際成功與否以同次報告為準。

原生版本政策目前只對已核對的 Qt／OpenSSL 公告設定最低版本；清冊中其他檔案的 `component_version` 可為空字串，表示未納入這份元件最低版政策，不代表已完成其內嵌元件的 CVE 清單。

## 仍需人工或其他方法確認

- 真正乾淨、一般使用者、斷網的 Windows 10／11 機器；CI 的空白資料與縮減 PATH 只證明指定隔離流程。
- 真人筆跡、任意掃描文件、觸控筆、混合 DPI、多螢幕、RDP、休眠恢復與不同外部軟體。
- 防毒誤報、SmartScreen 信譽、閉源或靜態嵌入庫的完整 SBOM／全部已知與未知漏洞，以及所有動態程式路徑。

上述限制保留為限制，不以「全部通過」蓋過。完成新增檢查後，仍只對同版雜湊、同一報告範圍及檢查當時負責。

## rc.19 實際資源門檻

`artifacts/resource-budget.json` 是額外必需證據，不代替任何既有安全／封網／500次模型推論門檻。外部host在onefile啟動前以suspended程序加入Windows Job，量cold_application及manual_invoice_and_helpers兩階段。後者跑三輪真Small模型核對、目前列、600萬像素有界輸入及影像helper；檢查收尾後無子程序、臨時檔已清。

模型壓測仍要求 Small、Medium 各完成500次、文字及模型身分一致。Windows 診斷 run `44075654073` 在舊900秒期限內已完成 Small 500次、Medium 400次，無辨識錯誤且CPU與進度持續增加；原期限截斷了正常工作。等待上限因此改為有限1500秒，保存載入／每10次進度與延遲，超時仍終止自有程序樹、等待回收並釋放handle。此診斷不是發行證據；正式同版EXE仍須重新完成全部1000次，1GB資源限制不變。

四項峰值各自嚴格小於1,000,000,000 bytes：process-tree RSS、採樣private、原生Job peak private、EXE＋活動_MEI解壓＋管理profile磁碟。20ms是目標採樣間隔，不當作連續任意時刻RSS證明；原生Job峰值另列，模型檔大小不取代實際用量。OS可能將配置上限向下按頁對齊；故意觸發拒絕的大額配置負例可能增加Job會計峰值，不是正常工作量通過證據。正式正常workload的原生峰值仍必須低於1GB。

公開報告只保留型別已驗證的峰值、計數、固定旗標、來源／EXE／同run雜湊，不公開逐樣本trace、路徑、文件影像或OCR文字。GUI原生focus報告在探針暫存中驗證後回收，公開只保留required/verified。
