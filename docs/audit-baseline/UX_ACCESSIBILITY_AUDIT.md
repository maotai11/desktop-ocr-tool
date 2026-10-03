# Native UX / Accessibility / Visual Quality Audit

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## Scope / aspiration benchmark

本專案為PySide6native desktop。Webby、Awwwards、FWA、CSSDesignAwards、W3、Lovie、UXDesignAwards、iF、RedDotCommunicationDesign、GoldenPin只作「資訊清楚、可操作、可恢復、可讀」的UXaspiration，不借其名稱宣稱已得獎、符合評審分數，或OCR/安全/效能已达標。沒有移植WebCSS或重做視覺。

已檢視actualoffscreen FloatingWidget/MainWindow/Settings(no-tag) PNG，另執行dialogs/exports/batch相關pytest與runtimeprobes。正式Settings有TagRepo路徑仍NameError，所附PNG是no-tag診斷，不能冒充正式可開啟。NativeWindows字型、DPI、Narrator、highcontrast、滑鼠與多monitor實測UNKNOWN。

## Evidence-driven UX matrix

| Dimension | Observed behavior / code | Classification / consequence | Verification / proposed correction |
|---|---|---|---|
| Visual hierarchy | 浮窗頂部OCR/截圖、search、filters/cards；主控台sidebar/table/detail | CONFIRMED結構分區明確；未做使用者時間量測 | preserveareas，以主要任務find/copy/review時間與錯操作率評，不重畫全部 |
| Density | 320×480widgetsource10px，table多row，右detail大空間 | CONFIRMEDscreen；小次文字3.235ratio，U01 | readablefont/contrast及differentwindowdimensions，語言不截斷重要金额 |
| Interaction consistency | tableExtendedSelection/Ctrl/Shift與顯式batchmode；widgetcard自訂選取 | source＋tests涵蓋tablebatchcontext，widgetmouse-only | keyboardfocus/action一致，以sameitemID/selectioncount回歸 |
| Loading | 真engine_progress/ready/failed→disabledOCRbutton/4pxbar/text | ACTIVE不是UI-only；初始截圖按鈕仍可用 | loadslow/error/queuedcapture，ready前後按鈕符合actualcapability |
| Progress feedback | stages10/30/60/80、load0/20/90/100，不是逐字進度 | CONFIRMEDdispatch；percentage粗略不反映時間 | distinguishphase/pending/current，測P95長階段不假承諾剩餘秒數 |
| Error recovery | failurestatus/message；rawdone/failed即刪，rerundisabled | CONFIRMEDD02/D03；4Kemptydone沒有review | 先fixsourceack/retention/status，才能設retryUI；保留原文字/候選 |
| Keyboard | QTablewidgetnative多選、Escbatch；buttonsQt可Tab；ItemCard無focuspolicy/keyaction | CONFIRMEDlimited，NarratorsemanticUNKNOWN | focusorder、Enter/Spacecard、sameactionshortcut、WinNarratorname/role |
| High DPI | Qt logicalgeo×primaryDPR→fixedMSS1；fonts硬px | sourceconfirmed，mixedDPI錯位INFERRED | 125/150/175/200全矩陣nativegate，不把offscreen當Windowspass |
| Focus / selection | styles對SpinBox/LineEdit/QCombo focus存在，tableselectionvisible | CONFIRMEDpartial；卡片focus缺、selected/hover狀態易區別否UNKNOWN | 各statepixel/keyboardwalk，selected及focus不用只有顏色提示 |
| Contrast | meta#636e8a on#1c1f28約3.235:1、10px | measured；以4.5normaltext作aspirationtarget，不宣稱nativeWCAGcertification | highlight/status/inactive/focus/background逐pair算並nativeview |
| Empty state | widget「無文字/空列表」部分文案；table/rightdetail未選時大片空白 | screenshot/sourceconfirmed；initialtaskorientation未usability測 | 清楚下一步capture/search/select，不能遮擋datafilter判斷 |
| Batch operations | deletedconfirmN與selectioncounter、context保持 | existingtests部分pass，真正queuedDbslot；不是全slop | activefilter/search/archived/empty/largeN exactIDs；tx/ack失敗feedback |
| Search | Enter/button與FTSquoted＋LIKEfallback；noquick_searchhotkey | CONFIRMEDfeature，literalwildcard及CJKFTSperformance限制 | 任意引號/中文subword/%/underscore、大history latency；選擇明確literalvswildcard |
| Settings IA | primary/secondary/autoswitch又legacyadvanced，名稱缺factory能力 | CONFIRMEDF02/F03/F04，正式pageF01fail | canonicalengine/conditions一套contract；先runtime再將UI與doc對齊 |
| OCR confidence | 平均幸存boxes；numeric在Info，lowbadge/editorconfirm | CONFIRMEDfunctionalreview但漏字仍done | displayuncertainty/candidate/missingregion與真identity，不能只把平均改百分比 |
| Review workflow | plaintextedit、HTMLnote、confirm；raw缺失而rerun不可；blankedit退raw | CONFIRMEDD01/D02/D04 | preserveoriginal/corrected/candidate/useraccepted，unknownmodel不補猜 |
| Tags | add/deletecode、edit/apply/usageUI-only＋missingimports | CONFIRMEDF01/F05 | onlyworkingcontrols可見／真RepoAPI、selectedIDsfeedback |
| Export | current可工作，allAttributeError，CSVformula，same秒覆蓋 | CONFIRMEDF06/S03/D05 | 範圍清楚、成功ack、不同filename、safeCSV不破坏原文字 |
| Clipboard privacy | traypause有效；settingcheckbox非live、default收全文 | CONFIRMEDS01/F07 | 可見collection狀態、實時toggle、consent/expirationfakecanarygate |
| Native system | traymenu、Winregisterhotkeys/code，restart/sleep/RDP未測 | UNKNOWN實際platformdurability | Explorerrestart/WMshutdown/nohotkeyregistration時可見fallback |

## Visual evidence

- `evidence/widget.png`：actualwidget、300loadrecords的fixture，標示OCR載入中；model是stub，不當成accuracyproof。
- `evidence/console.png`：actualmainview、301fakeitems，table/context可視；不是多monitor/liveclipboardprivacy驗收。
- `evidence/settings.png`：no-tagdialogconstruction可用，正式tagroute錯誤另見lifecycle.json。

Info面板顯示text/status/confidence/path，但沒有engine/model/hash；UI內providername若仍寫v5而runtime不同，不能加更漂亮badge掩蓋provenance。需要回溯的semanticdata先由D01生成。低confidence提示只在reviewbar，看不到det漏字；O03必先修候選與完整性呈現依赖。

## Native accessibility / Windows gate（unrun）

WindowsNarrator讀順序/name/role、鍵盤全流程（capture取消/selection/search/edit/tag/export/settings/trayquit）、高contrast與選擇/focus雙重state；125/150/175/200%不同screens、negativecoords、monitorunplug/sleep/RDP；文字缩放/繁中字型fallback、窄視窗溢出/scroll、modalerror回復焦點。沒有此gate不評awardgrade或合規通過。

建議修序：F01能開設定→F02/F03/F04消除假能力→D02/C02保留可恢復state→O01/O02/D01結果/identity→U01readability/keyboard→其餘視覺密度。功能與rootcause未成立前不能靠新style或更多loading文字宣稱ProductionUX提升。
