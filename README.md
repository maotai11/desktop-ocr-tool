# DesktopOCRTool

離線 Windows 桌面 OCR 工具。截圖即辨識，結果存入本地資料庫，不需連線、不上傳任何資料。

---

## 功能特色

- **即時 OCR**：熱鍵截取任意區域 → 自動辨識 → 存入控制台
- **主引擎**：RapidOCR PP-OCRv4（ONNX Runtime，離線輕量）
- **Group A — OCR 精準度補強**：前處理管線（去斜、去噪、對比增強、陰影移除、Sauvola 二值化）+ second-pass 重試
- **第二引擎 fallback（optional）**：PP-OCRv5 Mobile via PaddleOCR，手寫或低信心時自動觸發
- **批量操作**：控制台多選刪除、搜尋保持、分類保持（Group B）
- **完全離線**：DB SQLite，無網路相依，可打包為單一 EXE

---

## 安裝

### 核心依存

```bash
pip install -r requirements.txt
```

### 第二引擎（可選，預設不需要）

第二引擎為 **optional fallback**，不安裝不影響主流程：

```bash
pip install paddlepaddle      # CPU 版；GPU 版換 paddlepaddle-gpu
pip install paddleocr
```

安裝後，在 **Settings → OCR → 第二引擎** 勾選啟用。

---

## 快速上手

```bash
python main.py
```

首次啟動約需 60 秒載入 OCR 模型，狀態列顯示「OCR 就緒」後即可使用。

| 熱鍵 | 動作 |
|------|------|
| `Ctrl+Shift+O` | 區域截圖 OCR |
| `Ctrl+Shift+S` | 區域截圖存圖 |
| `Ctrl+Shift+F` | 全螢幕 OCR |

---

## 第二引擎 fallback（Handwriting OCR）

> 此功能為 optional；paddleocr 未安裝時，主引擎正常運作，僅 fallback 不生效。

**觸發條件（任一）：**
- 手寫模式（`mode=handwriting`）且「手寫觸發」開啟
- 主引擎信心低於門檻（預設 0.85）且「低信心觸發」開啟

**設定路徑：** Settings → OCR 分頁 → 「第二引擎 (Handwriting OCR)」

診斷標籤會即時顯示安裝狀態（綠色 = 可用，橘色 = 未安裝）。

詳細打包說明：[PACKAGING_NOTES.md](PACKAGING_NOTES.md)

---

## 打包為 EXE

```bash
cd artifacts
pyinstaller DesktopOCRTool.spec
# 產出：dist/DesktopOCRTool.exe（約 150-200 MB）
```

`paddleocr` 已從 `.spec` excludes 中排除，預設 EXE **不包含第二引擎**（體積控制）。

---

## 開發與測試

```bash
pip install -r requirements-dev.txt
pytest                  # 全套 128 tests
pytest --tb=short -q    # 簡潔輸出
```

**手動 smoke test：** [SMOKE_TEST_CHECKLIST.md](SMOKE_TEST_CHECKLIST.md)

**OCR 效果比較報告：** [docs/OCR_COMPARISON_REPORT.md](docs/OCR_COMPARISON_REPORT.md)

---

## 專案結構

```
desktop-ocr-tool/
├── src/
│   ├── app.py                  # 應用入口、引擎初始化
│   ├── ocr/
│   │   ├── engine.py           # RapidOCR 主引擎
│   │   ├── secondary_engine.py # SecondaryEngineBase 介面
│   │   ├── providers/          # optional provider adapters
│   │   │   └── paddleocr_v5_provider.py
│   │   ├── preprocess.py       # Group A 前處理管線
│   │   └── postprocessor.py
│   ├── core/config.py          # 設定管理（含第二引擎 5 個新 key）
│   └── ui/settings_dialog.py   # Settings GUI（含第二引擎診斷）
├── tests/                      # pytest 128 tests
├── artifacts/DesktopOCRTool.spec
├── PACKAGING_NOTES.md
├── SMOKE_TEST_CHECKLIST.md
└── docs/
    ├── OCR_COMPARISON_REPORT.md
    ├── OCR_SAMPLE_TEST_LOG.md
    └── REVIEWER_CHECKLIST.md
```

---

## 授權

本專案僅供個人離線使用。
