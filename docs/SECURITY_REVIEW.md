# 套件與背景工作檢查

套件修補基準：rc.5（2026-11-02）；背景工作說明更新：rc.6（2026-11-03）。本文件說明修正與檢查方法；每次正式發布的實際結果以同一來源版本的 validation metadata 為準。

## 已知漏洞與套件更新

使用 pip-audit 2.10.1 查詢實際安裝的 Python distribution，不只掃描手寫的 requirements。原環境有下列已知公告；同一漏洞可能同時帶有 CVE、GHSA、PYSEC 等別名，因此不把原始公告筆數當成不同 CVE 數量。

| 原版本 | rc.5 修正後版本 | 處理 |
| --- | --- | --- |
| Pillow 12.1.1 | 13.0.0 | 更新圖片解碼套件；原版命中多筆公告，包括 CVE-2026-40192、CVE-2026-42308 等 |
| setuptools 84.0.0 | 84.2.1 | 修正 GHSA-xmj3-9gf7-h52w |
| pip 25.0.1 | 26.2.2 | 更新建置安裝工具；原版命中 CVE-2025-8869、CVE-2026-1703 等。pip 不隨 EXE 提供 |
| OpenCV 4.10.0.84 | 5.0.0.93 | 避開仍內含舊 OpenEXR 的 4.x wheel；本版建置資訊必須為 OpenEXR: NO，封裝排除非本工具所需的 FFmpeg 影片 DLL |

rc.5 本機更新後的 31 個 Python distribution 掃描結果為 0 個已知漏洞；這是該次檢查紀錄，不沿用為後續版本的掃描結果。CI 會另外掃描 Linux 與 Windows 的實際安裝清單，Windows 專用套件也在範圍內。掃描工具裝在另一個環境，不包進 EXE。任何套件漏掃、資料庫查詢失敗、版本不符或發現漏洞都會阻止建置／發布；不使用忽略清單。

發布器會核對 Windows 掃描的來源 commit、安裝清單 SHA256、套件數量、掃描器版本及零發現結果。公開摘要提供套件數與清單雜湊；原始建置路徑及診斷輸出不放入公開 ZIP。

Python 公告資料庫不等於所有原生 DLL 的完整 CVE 覆蓋。PDFium、Qt、ONNX Runtime、影像編解碼器及原生手寫引擎仍須持續跟進上游；本次未完成逐一原生元件的完整 SBOM／二進位漏洞認證。檢查通過只代表當時資料庫未命中，不代表不存在未知漏洞。

## rc.6 新增的 A／B 連續核對

A／B 核對是使用者明確開始後執行的持續工作，不會在啟動應用程式或只打開核對窗時自行掃描。每輪從同一張主螢幕快照擷取兩區，再使用既有 OCR 佇列依序處理；前一輪完成後才等待設定間隔並開始下一輪，不堆積重複要求。

暫停、重新框選、關閉核對窗或退出程式時，會取消後續輪次並忽略過期結果；已進入原生辨識的呼叫仍需先返回。擷取圖片與結果不寫入歷史、剪貼簿或內容 Log，也不傳送到網路；座標只保留在這次程式執行中。此模式必須持續讀取螢幕並執行 OCR，因此啟用期間會使用 CPU；不需要核對時請按暫停。

## 功能結束後的程序與工作

- 手寫原生 helper 在回傳候選後收到結束訊號並被回收，不再一直留在背景等下一次。下一次書寫重新啟動；逾期未結束的程序會被終止。
- 新筆畫、清除或重新辨識會取消過期手寫要求；晚到結果不能改寫新結果。影像補辨也有獨立程序、期限與回收機制。
- OCR 模型載入後仍留在記憶體供下一次操作使用，但 ONNX Runtime 的執行緒等待已停用 CPU spinning，閒置時採阻塞等待。
- 文件的停止、清除與關窗會取消後續頁面、移除佇列並丟棄舊結果。正在 QThread 內執行的原生 PDF／OCR 呼叫需先返回，不能安全地強制打斷；這個限制仍存在。
- 系統匣、全域快捷鍵、資料庫與工作佇列等待是程式正常功能。關閉功能視窗不等於退出程式；要全部結束請從系統匣選「退出」。剪貼簿監看預設關閉，只有使用者啟用後才持續監看。
- 停用 ONNX 遙測及本工具不需要的 OpenEXR、Jasper、FFmpeg、GStreamer 路徑。模型只在建置時準備，應用程式不自動下載模型或更新。

## 網路觀察與驗證範圍

rc.5 的 Linux 檢查以 strace 追蹤主程序及其子程序，執行 OCR／資料庫、程式啟閉、筆畫手寫、影像補辨、PDF／圖片套版共五組合成探針。該次五組均完成，未觀察到 AF_INET／AF_INET6 網路系統呼叫；本機 AF_UNIX 通訊不計為網際網路。原始追蹤檔只存在暫存，結束即移除，公開報告只有計數與結果。rc.6 另加入 A／B 核對，追蹤共六組合成路徑；新增功能的檢查範圍與結果應查看該版 validation metadata，不以 rc.5 的五組結果代替。

Windows 另對同一份 EXE 與手寫 helper 套用對外防火牆阻擋，執行功能探針及兩組 OCR 模型各 500 次推論，並檢查單一 EXE 在空白使用者資料與僅系統 PATH 的環境啟動。這證明這些路徑可在阻擋連線時運作，不能當成 Windows 所有功能從未嘗試連線的證明。

上述檢查使用合成資料，未覆蓋所有 GUI 操作、任意外部文件、作業系統服務或未知原生程式碼。Windows／第三方雲端剪貼簿若已啟用，仍可能同步複製內容；本工具的歷史、截圖、附件、匯出與 Log 是未加密的本機資料。Repo 歷史與舊版公開附件另見 [隱私檢查](https://github.com/maotai11/desktop-ocr-tool/blob/master/docs/PRIVACY_AUDIT.md)。

## 在建置機重跑

安裝依賴並產生 `artifacts/dependency-inventory.json` 後，在獨立 venv 安裝 `requirements-security.txt`，使用應用程式的 Python 執行：

```sh
python scripts/audit_dependencies.py --inventory artifacts/dependency-inventory.json --tool-python <掃描用venv的Python路徑> --report artifacts/security-audit.json
# Linux，需先安裝 strace、準備模型與原生 helper：
python scripts/audit_runtime_network.py --report artifacts/runtime-network-audit.json
```

CI 會自動設定來源版本並執行相同指令；離線使用者不需執行或安裝掃描工具。
