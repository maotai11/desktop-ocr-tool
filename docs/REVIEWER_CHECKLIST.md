# 修復候選 Review

- [ ] 核對 `docs/VALIDATION_STATUS.md` 與實際 CI／artifact hash；不得將候選寫成已驗收 Release。
- [ ] 閱讀 QueueWorker／Pipeline，確認停止後的新工作明確拒絕，accepted jobs 與最後一次 DB barrier 的順序。
- [ ] 人工查核 SQLite rollback、OCR failed/source preservation、result provenance、空字串編輯。
- [ ] spacing/fusion fixture 的語言及座標 Ground Truth 符合案例；低 IoU／旋轉文字等未測範圍明列。
- [ ] 檢查 provider tests 使用 `.predict()` 與 3.x Mapping／`rec_polys`；不以 mock 宣稱 native integration。
- [ ] 配置 UI 的每個可操作控制項有 consumer；未實作項目不能只顯示儲存成功。
- [ ] Microsoft 連線歷史 payload 未知；opt-out、阻斷與網路事件歸因是不同證據。
- [ ] 新 profile 不監聽剪貼簿；舊 profile 行為與 retention 限制已寫明。
- [ ] Core build 內含實際模型、无 optional package 收集／排除衝突；取得乾淨 Windows 驗收。
- [ ] 避免以測試數量、模型檔名、離線宣稱或設計獎 benchmark 代替實證。
