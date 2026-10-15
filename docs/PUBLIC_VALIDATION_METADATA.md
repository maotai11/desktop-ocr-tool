# 公開 review CI 的證據界線

本儲存庫為公開專案，Actions artifacts不能視為私人儲存。本次review CI只上傳去識別摘要，不提供EXE或ZIP下載，也不構成Release交付。

## 原始驗證照常執行

測試收集／執行、native helper、source／frozen／阻斷對外連線probes、實際ZIP與payload核對仍以runner上的原始證據及原始hash驗收。摘要工具不覆寫原報告、不改寫原ZIP、不刪除失敗以取得通過。

只有新建的 `artifacts/public-validation/` 可上傳，且摘要工具必須成功；該目錄僅含 `summary.json` 與另行計算的 `summary.sha256`。目錄已存在、symlink／junction、越界、大小超限或不合法報告會拒絕或留下固定錯誤狀態，不複製任意例外文字。

公開摘要包含：

- 精確source commit、固定步驟的outcome與conclusion，保留失敗、取消、跳過與缺少狀態
- 每個固定來源檔的存在狀態、實際大小及由原始位元組計算的SHA-256；EXE／ZIP只有大小與hash，沒有上傳其內容
- JUnit一致性核對後的tests／failures／errors／skipped總數，不含testcase名稱、hostname、properties或trace
- JSON固定容器內通過型別／固定enum／模型版號／hex hash／有限數值檢查的欄位；原始error／trace／command／path／環境內容不複製
- 原始記錄的pass旗標與失敗內容或步驟結果衝突時，明列不一致；缺報告或解析失敗不視為PASS

原始證據SHA識別未上傳的runner-local檔案；摘要自身SHA則識別公開摘要，兩者不能互換。摘要不是原始JUnit、完整log或可重新驗收的成品包。固定成功control也不是手寫真人準確率證明。

## 發布仍阻塞

一般review分支不命中舊publisher的精確branch／commit marker。即使另行命中舊opt-in，metadata-only artifact也不具備publisher要求的原始候選資料，應拒絕發布。

公開可下載候選必須另製去識別衍生包，重新計算包及內容hash，並對該實際位元組重新驗證；不得將原始ZIP報告改成摘要卻沿用舊hash。新Windows完整suite、onefile／PID／Job／reap、乾淨機與真人筆跡等未驗收範圍仍保持開放。

Actions一般console仍可見官方hosted runner標準建置路徑與診斷。本次CI只使用公開鎖定資產和合成fixture，不注入使用者本機環境、私人測資或新憑證；不將公開runner當作私人環境。
