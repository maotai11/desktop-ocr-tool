# 手動 Smoke 驗收清單 — v1.0.8 (Patch 1-7)

對應 Patch 1–7 中需要真實 GUI 或無法直接 import 測試的路徑。
自動化測試（pytest）覆蓋資料層與 Worker slots，本清單覆蓋其餘部分。

---

## 前置準備

- [ ] `pip install -r requirements.txt` 完成
- [ ] `python main.py` 啟動，無例外彈窗
- [ ] `logs/app.log` 存在，最後幾行含「Phase A 完成，進入事件迴圈」
- [ ] 等待 OCR 引擎載入，狀態列顯示「OCR 就緒」（首次約 60 秒）

---

## Patch 1 — MainWindow 多選批量軟刪除

| # | 步驟 | 預期結果 |
|---|------|---------|
| 1.1 | 有至少 5 筆資料時，Ctrl+A 全選 → 點「刪除」按鈕 | 彈出確認框，訊息含「N 筆」 |
| 1.2 | 確認刪除 | N 筆全消失，狀態列「已刪除 N 筆」2 秒後回「就緒」 |
| 1.3 | Ctrl+Click 選 3 筆（不連續）→ 刪除 → 確認 | 恰好 3 筆消失，其餘不受影響 |
| 1.4 | 單選一筆 → 刪除 | 確認框訊息含「ID: X」（單數形式，不顯示「N 筆」） |

---

## Patch 2/3 — 操作後保持分類 / 搜尋上下文

| # | 步驟 | 預期結果 |
|---|------|---------|
| 2.1 | 側欄點「文字」→ 右鍵某筆 → 釘選 | 列表仍在「文字」分類（不跳回「全部」） |
| 2.2 | 搜尋框輸入關鍵字 → 搜尋 → 刪除結果中一筆 → 確認 | 搜尋結果仍顯示（少了那筆），搜尋欄不清空 |
| 2.3 | 側欄點「已歸檔」→ 右鍵某筆「取消歸檔」 | 該筆從列表消失，仍停留在「已歸檔」檢視 |
| 2.4 | 側欄點「釘選」→ 右鍵某筆「取消釘選」 | 該筆消失，仍停留在「釘選」檢視 |
| 2.5 | 側欄點「全部」→ 右鍵釘選 → 切換到「釘選」→ 可見剛才釘的筆 | 跨分類操作不污染 |

---

## Patch 4 — CaptureWorker queue drain

| # | 步驟 | 預期結果 |
|---|------|---------|
| 4.1 | 在 1 秒內快速按 region_ocr 熱鍵（Ctrl+Shift+O）3 次 | 截圖覆蓋疊開；主控台最終新增 **3 筆**記錄（非只 1 筆） |
| 4.2 | 觀察主視窗 | 每次截圖後主視窗重新出現（`widget.show()` 被呼叫 3 次） |
| 4.3 | 觀察 `logs/app.log` | 出現 3 行「已儲存 item #X」，item id 不同 |

---

## Patch 5 — region_ocr path 不錯配

| # | 步驟 | 預期結果 |
|---|------|---------|
| 5.1 | 截取「A 畫面」→ 再截取「B 畫面」（分別 region_ocr）| 主控台中 A 筆的 OCR 文字對應 A 的縮圖；B 筆的 OCR 文字對應 B 的縮圖 |
| 5.2 | 觀察 `logs/app.log` | 無 `KeyError`、`None`、`AttributeError: 'NoneType'` 等 WARNING/ERROR |

---

## Patch 6 — OCR cleanup 一致性（success + exception 都清理）

| # | 步驟 | 預期結果 |
|---|------|---------|
| 6.1 | region_ocr 截取含文字畫面（OCR 成功） | `logs/app.log` 出現「已刪除 OCR 暫存圖片: ...」 |
| 6.2 | region_ocr 截取純黑 / 空白畫面（OCR soft-fail，status='failed'）| log 仍出現「已刪除 OCR 暫存圖片」（cleanup 路徑一致） |
| 6.3 | 上述操作後，檢查 `data/captures/YYYY/MM/` 目錄 | 無殘留 `YYYYMMDD_HHMMSS_*.png` 暫存圖 |

---

## Patch 7 — DB raw_image_path 清空

| # | 步驟 | 預期結果 |
|---|------|---------|
| 7.1 | region_ocr 完成後 → 主控台選該筆 → 右側「資訊」tab | 「圖片路徑: (無)」（raw_image_path 已清空） |
| 7.2 | 同一筆 → 右側「預覽」tab | **仍可見縮圖**（thumbnail_path 保留，不顯示「圖片不存在」） |
| 7.3 | 右側「文字」tab | OCR 識別文字正常顯示（text_content 未被 clear） |

---

## 整體回歸確認

- [ ] 以上所有步驟完成後，`logs/app.log` 最後不含 CRITICAL / ERROR 行
- [ ] 系統列右鍵退出 → 程式正常結束（無掛起）
- [ ] 重新啟動 → 先前的資料仍存在（DB 持久化正常）

---

## 自動化測試執行方式

```bash
pip install -r requirements-dev.txt
pytest                        # 執行全部自動化測試
pytest tests/test_repository.py -v          # 只跑資料層
pytest tests/test_db_worker.py -v           # 只跑 DbWorker slots
pytest tests/test_capture_worker_drain.py -v  # 需要 Qt (pytest-qt)
```

預期：`19 passed`（11 repository + 6 db_worker + 2 capture_worker）
