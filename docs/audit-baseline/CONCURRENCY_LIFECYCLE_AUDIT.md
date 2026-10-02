# Concurrency / Lifecycle Audit

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## Thread ownership map

| Object / runtime | Creation / Qt affinity | Executing thread | Shared resources / paths |
|---|---|---|---|
| QApplication/UI/ClipboardWatcher/Overlay/Tray | Main Qt | Main / callbacks具體看context | repo reads、Editor/widget/tag writes |
| HotkeyListener QThreadobject | Main | run為Winmessage-pollnativeworker | _pending注册、_hotkeys、stop bool；Win API UNKNOWN |
| CaptureWorker QThreadobject | Main | run nativecaptureworker | thread-safequeue，MssBackend singleton但capture每次context新建；PNG/thumb/filemgr |
| OcrWorker QThreadobject / ordinary OcrEngine | Main | run load/OCR nativeworker | queue、modelready、mode、progress、secondary/config从main可改 |
| DbWorker QObject | movedDbQThread | Db eventloop | 與main共享SQLiteconnection/ItemRepo/FileManager |
| SQLite connection | Maincreated | main + Db calls | check_same_thread=False，不是exclusivetransaction ownership |
| ONNX Runtime sessions | OCR worker load | nativepools foreachdet/rec/cls | default與threads2實測threadcounts不同；SDKpool不是PythonThread |
| PaddleOCR provider | objectsetupmain，懶載入OCRworker | Paddle nativeCPUruntime | _lock只保護load，不保護主thread替換provider/config |

## Complete DB write consumers

| Call family | Actual caller thread / dispatch | Finding |
|---|---|---|
| startup schema/migrations | mainDatabase.initialize | 同conn初始化 |
| clipboard/capture insert | appthree-argQTimer(DbWorker)→save_item | 真Dbthread已trace |
| OCR update/pathclear | appthree-argQTimer(DbWorker) | queueorder有序不等於transaction/fileatomic |
| MainWindowsoftdelete/pin/archive/purge requests | typedQtSignal→DbWorkerslots | 移到Dbthread，UIrefreshcallback另排 |
| FloatingWidgetsinglepin | lambda直接_item_repo.set_pinned | 主threadwrite，反例 |
| Editor edit/note/confirm/rerunpending | directrepository | 主threadwrite；editedtext/note/confirmed三次commit非atomic |
| SettingsTagcreate/delete/add association | directTagRepository | 主threadwrite；目前建構bug但設計路徑實存 |
| filedeletion | DbWorkerpurge/delete；apprawcleanup | 可在Db或signalcallback thread，沒有與DBatomicack |

注：FTS5 triggers運行在同execute/transaction，不另創thread；WAL容許不同connection協調，不為同conn上兩個caller提供獨立transaction。

## 實際 stress 與受控 interleavings

| Probe | Result | Interpretation |
|---|---|---|
| OCR boundary100 actualQThread | 100/100第二任務queued但threadend後remaining1 | CONFIRMED可能schedule，刻意widenwindow不是fieldfrequency |
| Captureboundary actualQThread | 第二taskremaining1 | 同handoffrootcause |
| Db300queuedwrites＋UI300pin/read | noexception、rows301 | positive限定load；不可宣稱沒race |
| transactionboundary＋fault | save_failed且沒有saved signal，新INSERTrow仍committed | nativeSQLite sharedtransaction由UIcommit带入，fieldfaultfrequencyUNKNOWN |
| quitduringengine load | exit134，QThread destroyed stillrunning | realQt、actualappquitpath；只stubWindows服務及慢load，非Windows驗收 |
| settings/editorclosecycles | 500settings/100editorsretained | QObjectownership已實證，不看GC推理 |

DBtimeoutwait返回值忽略，quiteventloop可能不執行所有pendingevents；資料flush何時完成沒有barrier。update_ocr只logerror，app仍刪source/clearpath。NoOcr/Capturestop API，start_loading doublecall可二次start，檢查isRunning不是全lifecyclestate；actualdouble-startcrashfieldUNKNOWN。Enginefailedqueueditems沒有逐itemterminalack，任務可一直none/pending，raw保留。

## Windows / secondary lifecycle gaps

| Scenario | Current evidence | Required reproducible gate |
|---|---|---|
| quit during OCR /capture /DBlongwrite | source缺join; onlyloadabortexecuted | barrier每stage，pause/quit/allowcomplete，boundedtime、nothreadrunning、resultack |
| quit duringsecondary firstload/download | source缺协调；adapter本身损坏 | preloadlocalmodels、故意延load、callbackafterdelete、cachefilepartialcleanup |
| pendingcallback/deletedQObject | contextless2000/150ms source存在，crashUNKNOWN | 改P03/DIALOGdelete後delayeventdelivery，inspectwarnings及ItemID |
| onloadfailure queue | unresolvedsourcepath | 每queueditem收到failed/cancelled一次，無孤兒圖，restart可recovery |
| double start /rapid hotkeys | source state沒有atomiccontract | 跨thread1000tasks随机arrival＋boundary，exactonceIDs |
| Windows shutdown/sleep/resume/RDP/explorerrestart | UNKNOWN | physicalWinloop與WMsessionending/nativeQtclosing，proc/threadcounts與DBintegritycheck |

Fix以C01/C02/C03/D02 rootcause順序進行；先停止新work、明確cancel/drain與每job terminal state，等Db持久化ack後才能filecleanup與close。不能在mainwait仍依maincallback解除的worker造成deadlock；timeout不能當成功。修復gate需realstress／cleanWindows，不以新增tests存在稱race已解決。
