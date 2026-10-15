# 歷史驗證摘要（2026-10-03）

本頁是2026-10-15新增的去識別摘要，並非原始執行紀錄。為避免公開執行機路徑與主機名稱，14份歷史log、JUnit及含模型絕對路徑的JSON已移出目前待提交樹；原始位元組已另行保留供恢復核對。本次沒有重跑測試或推論，也沒有改寫Git歷史。舊提交仍可能含原始資料。

逐份原始檔名、大小、SHA-256及允許公開的結果欄位見 [機器可讀摘要](HISTORICAL_VALIDATION_20261003.json)。這些hash識別原始檔，不是本摘要的hash；目前樹沒有提供那些原始位元組，不能將本摘要冒稱為原始JUnit或完整log。

## 當年結果與版本界線

基準參照為 `bba210ec71c7d08d0093c11dba3964d80d0dfb27`。各列來自不同歷史階段，不能合併為一次測試；均不是本輪InkPick候選的Windows驗收。

| 歷史紀錄 | 原始XML所載tests | failures / errors / skipped | 秒 |
|---|---:|---|---:|
| 原修復整套紀錄 `full.xml` |156|0 / 0 / 0|10.750|
| 恢復基準 `baseline-tests.xml` |156|0 / 0 / 0|7.742|
| data/worker階段 `data-worker-full.xml` |292|0 / 0 / 0|8.826|
| 當年封存最終 `final-tests.xml` |384|0 / 0 / 0|9.020|

舊復原報告另曾描述較早的299 passed／8.73秒階段；那不是封存最終XML的內容。保留階段差異，不以384覆寫所有歷史數字，也不把任何一列宣稱為目前完整suite通過。

早期source self-test記錄 `passed=true`、Linux、Qt offscreen、`frozen=false`。恢復版schema2另記錄版本1.6.2-rc.2，Qt／models／OCR／database檢查通過，`clean_machine_verified=false`、`network_observation=NOT_RUN`。完整欄位允許清單見JSON；模型實際絕對路徑未複製到摘要。

兩份獨立seccomp guard記錄filter loaded、IPv4／IPv6／Unix新socket均為EPERM。這僅支持當次Linux程序內的socket拒絕檢查；沒有packet capture，不關閉繼承descriptor，不代表Windows斷網乾淨機器驗收。guard與self-test本身的網路觀察欄位分開保留。

## 仍留在目前樹的資料與限制

- 七張合成OCR像素、固定GT及逐案結果仍在 [OCR配對資料](ocr-recovery-20261003/inputs.json) 與 [結果](ocr-recovery-20261003/results.json)；這是合成比較，不能推廣為真實現場準確率。
- [source app smoke](recovery/source-app-smoke.json)、[SQLite lifetime](recovery/sqlite-lifetime.json) 與 [相依環境](recovery/pip-freeze.txt) 未因本次清理變更。
- [500次profiler](runtime-500.json) 及 [spacing replay](spacing-replay.json) 保留原資料；範圍見 [性能文件](../PERFORMANCE_VALIDATION.md)。
- Windows frozen EXE、實際斷網乾淨機、多螢幕／DPI、真實現場holdout與目前候選完整驗證，須另看 [目前驗證狀態](../VALIDATION_STATUS.md)。
