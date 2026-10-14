# InkPick 1.8.0-rc.1 離線候選建置

Windows EXE 必須在 Windows x64 建置，最低 Windows 10 1809（Qt 6.11 支援平台）。Linux 原始碼測試不能代替 Windows 成品。

## 固定輸入

使用 Python 3.12 與 requirements-dev.txt。依 README 順序準備兩組 PP-OCRv6 模型、官方 Tegaki 0.3 模型及固定 Zinnia 原始碼。Microsoft x64 C++ helper 使用 Release `/MT`，核對 Windows 系統 DLL imports；SHA256 使用 Windows 10 起提供的 BCrypt API。不能交付 Linux helper、Debug CRT 或未驗證舊 DLL。

SVG 確定性產生 16–256px PNG／ICO。app、視窗及系統匣共用 PNG；EXE 的 PE icon 與產品名稱另核對實際資源。執行期不新增 QtSvg／WebEngine 匯入。

PyInstaller 使用明確白名單：兩組 OCR 權重及圖片、手寫模型／字表／16 筆原始模型筆跡 fixture、Windows helper／identity lock、InkPick raster assets。多餘 ONNX、缺少資產或 hash 不符皆拒絕。手寫模型約 49.8MiB；one-file 冷啟動解壓成本須在成品量測，不能拿 Linux helper 時間代填。

## 同一成品驗證

1. Windows／Linux tests 與 source OCR／SQLite／app lifecycle／手寫 probes
2. 同一 Windows EXE 的 payload、OCR 字表、helper source／x64／DLL imports、PE 圖示及產品名稱
3. frozen OCR／SQLite、app lifecycle、手寫候選／最新佇列／Unicode 剪貼簿／安全停止
4. 臨時 runner 封鎖外層 EXE；手寫 probe 啟動模型前以握手取得同一 one-file 程序已校驗的 helper 路徑，再封鎖該 helper
5. 規則生效時重跑 probes，Small／Medium 各 500 次推論，finally 移除本次規則
6. ZIP、EXE、模型、來源 commit、報告及同一 run 測試結果逐一核對

握手只控制驗證流程；app 不自行宣稱 OS 規則生效。PowerShell 讀回規則並記錄 hash-bound gate。完成的公開報告不保留 nonce 或 helper 暫存絕對路徑。

## 授權與來源

portable ZIP 的 handwriting-source 內含原始模型 XML、LGPL 2.1 文字、模型 manifest，以及固定 Zinnia BSD 原始碼／COPYING、資源界限修補及 helper/CMake 原始碼。建置與發布再次核對這些 bytes；不能只交付模型及上游連結。完整第三方相依／法務審查仍是獨立門檻，見 THIRD_PARTY_NOTICES.md。

## 固定發布門檻

只接受 maotai11/desktop-ocr-tool 的 fix/inkpick-handwriting 分支與精確 `publish-prerelease: v1.8.0-rc.1` commit marker。一般 push／PR 只驗證；不發布、不覆寫舊資產。必須用同一 run／commit 的新 Windows 成品，不沿用 rc.2 EXE 或舊結果。

GitHub runner 的系統元件可能掩蓋乾淨機器依賴。clean-machine、真人滑鼠／觸控筆跡、混合 DPI／多螢幕、RDP／休眠及 shell 圖示快取仍需現場驗收，僅發布 prerelease。既有 OCR QThread 永久不返回仍未隔離；手寫限時終止不代表 OCR hang 已解決。
