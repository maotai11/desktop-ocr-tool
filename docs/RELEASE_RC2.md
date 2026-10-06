# v1.6.2-rc.2 預發布測試版

這是已編譯的Windows x64候選版本，不是零錯誤或乾淨機器驗收保證。

## 下載與使用

建議下載portable ZIP，解壓到一般使用者可寫入的資料夾，再執行內含的`DesktopOCRTool-v1.6.2-rc.2.exe`。另提供同一個EXE與SHA256SUMS；EXE已內含Python／Qt／ONNX Runtime／v4三個模型，不要求目標機器安裝Python、pip或首次下載模型。

Release會同時提供小型validation metadata ZIP，包含同一EXE／portable ZIP的實際雜湊、source commit、native Windows probes、模型identity、測試與DLL/payload inventory。它不包含私人截圖。

啟動前核對SHA256SUMS。首次使用新設定預設不監聽剪貼簿；啟用後可能保存敏感內容。歷史、截圖、縮圖及匯出是明文，沒有自動到期清除。

## 已通過及仍有限制

同一候選必須重新通過Linux／native Windows tests、source OCR／SQLite與完整startup/shutdown、frozen EXE probes、Windows outbound firewall block，以及ZIP／EXE／embedded models／source manifests的實際byte核對，才發布為prerelease。正式stable驗收沒有因此代填。

- 罕字與小字仍可能錯辨；七圖合成量測中的罕字仍失敗，不能把confidence當文字完整性
- 切片結果待覆核，保留raw hypotheses；旋轉、直排、表格與真實現場holdout仍待驗證
- mixed DPI／多螢幕尚未實測，框選目前有primary-screen假設
- native OCR永不返回時可能阻止退出；目前安全drain與stall提示，不強制終止live DB threads
- 未簽章，完整模型／dependency license與可信hash lock仍未驗收

## 最小乾淨離線驗收

在新的Windows x64一般使用者環境，未安裝Python／pip／OCR／開發工具且無模型快取，斷網後搬入同一ZIP：

1. 記錄OS版本、ZIP／EXE SHA256；解壓、開啟、退出，再開啟一次
2. 執行內含`Validate-Candidate.ps1`兩種probe，保存`candidate-gate.json`；不修改系統安全政策來繞過拒絕
3. 測框選、取消、熱鍵、人工修改／重跑、退出與匯出；核对實際文字及原圖保留
4. 確認不缺DLL、不要求安裝／下載；記錄實際螢幕DPI及網路狀態

腳本的`clean_machine_verified=false`是刻意保留：機器安裝、快取、斷網及人工工作流程必須另行記錄。原生DLL完整性仍要由這個環境證实。

這次僅發布固定`v1.6.2-rc.2`，不合併master，不覆寫既有版本或資產，不自動發布以後的版本。
