# Reviewer / Maintainer 交付前確認清單

**對應版本：** DesktopOCRTool（Group A + Group B + H1-H4）
**用途：** 每次 release 或 code review 前逐項確認

---

## 一、自動化測試

```bash
pytest --tb=short -q
```

- [ ] 全套 **128 passed**，0 failed，0 error
- [ ] 唯一允許的 warning：`RequestsDependencyWarning: urllib3 ... requests` （paddleocr 相依，非本專案問題）
- [ ] 無新的 `DeprecationWarning` 或 `PendingDeprecationWarning` 來自本專案程式碼

**測試分布：**

| 測試檔 | Tests | 覆蓋範圍 |
|--------|-------|---------|
| `test_repository.py` | 11 | 資料層 CRUD |
| `test_db_worker.py` | 7 | DbWorker slots |
| `test_capture_worker_drain.py` | 2 | CaptureWorker queue |
| `test_ocr_preprocess.py` | 16 | Group A 前處理 |
| `test_main_window_batch.py` | 11 | Group B 批量選取 |
| `test_secondary_engine.py` | 19 | H1 介面 + fallback routing |
| `test_h2_providers.py` | 22 | H2 provider adapter |
| `test_h3_config_wiring.py` | 26 | H3 config / settings / diagnostics |
| `test_h4_fallback_edge_cases.py` | 14 | H4 3.x 格式 + 失敗路徑 |

---

## 二、功能完整性（主引擎路徑）

- [ ] `python main.py` 啟動無例外，狀態列達「OCR 就緒」
- [ ] 熱鍵 `Ctrl+Shift+O` 截取含文字畫面 → 辨識結果存入控制台
- [ ] `logs/app.log` 無 CRITICAL / ERROR 行（正常使用下）
- [ ] OCR 完成後，暫存圖已清除（無 `data/captures/` 殘留）

---

## 三、第二引擎 optional fallback

- [ ] **paddleocr 未安裝時**：主流程完全正常，Settings 診斷標籤顯示橘色「未安裝」
- [ ] **paddleocr 已安裝時**：Settings 診斷標籤顯示綠色「已安裝，可使用」
- [ ] `enable_secondary_engine` config key 預設值為 `false`（不影響新使用者）
- [ ] 第二引擎關閉狀態下：`logs/app.log` 不出現「觸發第二引擎 fallback」
- [ ] 第二引擎開啟 + 主引擎高信心：不觸發 fallback（不影響效能）

---

## 四、設定持久化

- [ ] Settings 變更後儲存，重新啟動數值保留
- [ ] 舊版 config 檔（缺少新 key）升級時，`_merge()` 自動補入預設值，不崩潰
- [ ] 以下 5 個第二引擎 config key 均有正確預設值：

  | Key | 預設值 |
  |-----|--------|
  | `enable_secondary_engine` | `false` |
  | `secondary_engine_provider` | `"paddleocr_v5_mobile"` |
  | `secondary_engine_for_handwriting` | `true` |
  | `secondary_engine_for_low_confidence` | `true` |
  | `secondary_engine_confidence_threshold` | `0.85` |

---

## 五、打包 EXE

```bash
cd artifacts && pyinstaller DesktopOCRTool.spec
```

- [ ] 打包無 ERROR（WARNING 可忽略）
- [ ] 產出 `dist/DesktopOCRTool.exe` 存在，體積約 150-200 MB
- [ ] `dist/DesktopOCRTool.exe` 在乾淨 Windows 機器（無 Python）可啟動
- [ ] `.spec` 的 `excludes` 清單含 `'paddleocr'`（第二引擎不打進 EXE）
- [ ] EXE 啟動後 OCR 功能正常（未因 paddleocr 缺失而崩潰）

---

## 六、回歸（smoke test 快速路徑）

參考 [SMOKE_TEST_CHECKLIST.md](../SMOKE_TEST_CHECKLIST.md)，至少驗證以下核心路徑：

- [ ] Smoke 1.1-1.4：批量選取刪除
- [ ] Smoke 4.1：CaptureWorker queue drain（快速連續 3 次熱鍵）
- [ ] Smoke 6.1：OCR 成功後暫存圖清理
- [ ] Smoke B.1-B.5：Group B 選取模式 UX
- [ ] Smoke H-A.1-A.5：第二引擎 Settings 接線（不需安裝 paddleocr）

---

## 七、文件完整性

- [ ] `README.md` 存在，含 optional paddleocr 說明
- [ ] `PACKAGING_NOTES.md` 存在，含 excludes 說明
- [ ] `SMOKE_TEST_CHECKLIST.md` 存在，包含 H1-H3 smoke 章節
- [ ] `docs/OCR_COMPARISON_REPORT.md` 存在（驗收報告模板）
- [ ] `docs/OCR_SAMPLE_TEST_LOG.md` 存在（樣本紀錄模板）
- [ ] `docs/REVIEWER_CHECKLIST.md` 存在（本檔案）

---

## 八、程式碼品質（快速目視）

- [ ] H1-H4 新增的 `src/` 程式碼無明顯 TODO / FIXME / HACK 留存
- [ ] `src/ocr/providers/paddleocr_v5_provider.py` module-level import guard 存在
- [ ] `src/ocr/secondary_engine.py` NullSecondaryEngine 為預設，無 None 暴露
- [ ] `src/app.py` step 9b 的 `create_provider` import 在 `if cfg.get(...)` 內（不影響未啟用時的載入）

---

## 九、交付狀態確認

| 項目 | 狀態 |
|------|------|
| 128/128 tests passed | □ 確認 |
| EXE 可在乾淨機器執行 | □ 確認 □ 未測試 |
| Smoke 核心路徑 | □ 全過 □ 部分未過（說明：） |
| OCR 比較報告填寫 | □ 已填 □ 待填 |
| 已知缺陷清單 | □ 無 □ 有（見下） |

**已知缺陷 / 待處理：**

| # | 描述 | 嚴重度 | 計畫 |
|---|------|--------|------|
| | | | |

---

**簽核：** _______________  **日期：** ____年____月____日
