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

## Group B — 批量選取 UX 補強

| # | 步驟 | 預期結果 |
|---|------|---------|
| B.1 | 主控台右上角點「選取模式」 | 按鈕高亮（accent 色），搜尋列下方出現 action bar；status bar 顯示「選取模式 — ...Esc 離開」 |
| B.2 | 點任意 3 行（單擊即選，不需 Ctrl）| action bar 顯示「已選 3 筆」，「刪除所選」由灰變紅啟用 |
| B.3 | 點「全選」 | 所有行高亮，計數 = 表格總行數 |
| B.4 | 點「清除選取」 | 所有高亮消失，計數回「已選 0 筆」，「刪除所選」停用 |
| B.5 | 選取 3 筆 → 點「刪除所選」→ 確認 | 彈確認框（含「3 筆」），確認後 3 筆消失，計數歸 0 |
| B.6 | 搜尋關鍵字 → 進入選取模式 → 選幾筆 → 刪除 | 刪除後搜尋結果保持（列表減少，搜尋欄不清空） |
| B.7 | 側欄切「釘選」→ 進入選取模式 → 刪除一筆 | 刪除後仍在「釘選」分類（不跳回「全部」） |
| B.8 | 點「離開選取模式」 | action bar 消失，按鈕回預設態，detail panel 清空，status bar 回「就緒」 |
| B.9 | 離開選取模式後點任意行 | detail panel 右側正常更新（預覽 / 文字 / 資訊） |
| B.10 | 進入選取模式後 → 搜尋框仍可輸入 | 選取模式不鎖定搜尋框，Esc 在搜尋框內不觸發退出模式 |
| B.11 | 進入選取模式後按 Esc（焦點在 table） | 退出選取模式，action bar 消失 |
| B.12 | Ctrl+Click 選不連續行 | 計數正確更新，多選仍可用 |
| B.13 | 右鍵點選 table 中一行（選取模式下）| 右鍵選單最頂端顯示「刪除已選取（N 筆）」 |
| B.14 | 選取模式下 detail panel 右側 | 不因單擊 row 而更新（不殘留舊筆資訊） |

---

## Patch H1-H3 — 第二引擎（Handwriting OCR Second Engine）

> 前置條件：Settings → OCR 分頁 → 「第二引擎」區塊中，勾選「啟用第二 OCR 引擎」。
> 若 paddleocr 未安裝，診斷標籤會顯示「未安裝（pip install paddleocr）」，以下 H-A 至 H-D 僅在安裝後有效。

### H-A：Settings 接線（不需安裝 paddleocr 即可驗收）

| # | 步驟 | 預期結果 |
|---|------|---------|
| A.1 | 開啟 Settings → OCR 分頁 | 可見「第二引擎 (Handwriting OCR)」QGroupBox |
| A.2 | paddleocr 未安裝時，觀察診斷標籤 | 顯示「未安裝（pip install paddleocr）」橘色 |
| A.3 | 勾選「啟用第二 OCR 引擎」→ 儲存 → 重新開啟 Settings | 勾選狀態持久化 |
| A.4 | 調整信心門檻 SpinBox → 儲存 → 重新開啟 | 數值正確保留 |
| A.5 | 取消勾選「手寫/低信心 觸發」→ 儲存 | 儲存不報錯；下次開啟設定值正確 |

### H-B：第二引擎 fallback 行為（需安裝 paddleocr）

| # | 步驟 | 預期結果 |
|---|------|---------|
| B.1 | pip install paddleocr → 重開 Settings | 診斷標籤變「已安裝，可使用」綠色 |
| B.2 | 啟用第二引擎 → 截取手寫圖片（region_ocr） | `logs/app.log` 出現「觸發第二引擎 fallback [paddleocr_v5_mobile]」 |
| B.3 | 同上 → 觀察主控台識別結果 | 識別文字應優於或等於 RapidOCR 主引擎結果 |
| B.4 | 截取清晰印刷文字（高信心）→ 觀察 log | 不應出現「觸發第二引擎」— 主引擎信心足夠，不 fallback |
| B.5 | 取消勾選「啟用第二 OCR 引擎」→ 儲存 → 截取手寫圖片 | log 中不出現「觸發第二引擎 fallback」 |

### H-C：細粒度觸發控制（需安裝 paddleocr）

| # | 步驟 | 預期結果 |
|---|------|---------|
| C.1 | 取消勾選「手寫觸發」，保留「低信心觸發」→ 截取手寫圖片 | 主引擎信心高時，不觸發第二引擎 |
| C.2 | 調低信心門檻（如 0.60）→ 截取同一圖片 | 主引擎信心若低於 0.60，觸發第二引擎 |
| C.3 | 取消勾選「低信心觸發」，保留「手寫觸發」→ 以 mode=handwriting 截圖 | 手寫模式觸發，低信心模式不觸發 |

### H-D：懶惰載入與啟動效能

| # | 步驟 | 預期結果 |
|---|------|---------|
| D.1 | 啟用第二引擎 → 啟動 app → 觀察 `logs/app.log` | 無「載入 PaddleOCRv5」訊息（首次 recognize() 才載入） |
| D.2 | 第一次觸發第二引擎 → 觀察 log | 出現「載入 PaddleOCRv5 Mobile 引擎」，且只出現一次 |
| D.3 | 第二次及後續觸發 | 不再出現「載入」訊息（模型已快取） |

---

## 整體回歸確認

- [ ] 以上所有步驟完成後，`logs/app.log` 最後不含 CRITICAL / ERROR 行
- [ ] 系統列右鍵退出 → 程式正常結束（無掛起）
- [ ] 重新啟動 → 先前的資料仍存在（DB 持久化正常）

---

## 自動化測試執行方式

```bash
pip install -r requirements-dev.txt
pytest                                          # 執行全部自動化測試
pytest tests/test_repository.py -v             # 只跑資料層
pytest tests/test_db_worker.py -v              # 只跑 DbWorker slots
pytest tests/test_capture_worker_drain.py -v   # 需要 Qt (pytest-qt)
pytest tests/test_ocr_preprocess.py -v         # OCR 前處理（Group A）
pytest tests/test_main_window_batch.py -v      # 批量選取狀態機（Group B）
pytest tests/test_secondary_engine.py -v       # H1: 第二引擎介面 + fallback routing
pytest tests/test_h2_providers.py -v           # H2: provider adapter (paddleocr 可不裝)
pytest tests/test_h3_config_wiring.py -v       # H3: config / settings / diagnostics
pytest tests/test_h4_fallback_edge_cases.py -v # H4: 3.x 格式映射 + 失敗路徑邊界情況
```

預期：`114 passed`（含 H1-H4 第二引擎全套）
