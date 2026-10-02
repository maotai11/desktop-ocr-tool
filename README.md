# 桌面 OCR 擷取工具

目前修復分支：**1.6.2-rc.1 候選版本，尚未通過乾淨 Windows 離線機器驗收。**
最新可核對狀態：[VALIDATION_STATUS](docs/VALIDATION_STATUS.md)。舊版 v1.6.1 的問題不能因候選版本測試通過就全部視為結案。

目標是 Windows x64 可攜式應用：在有網路的建置電腦準備完整套件，再將 ZIP 搬到離線電腦，解壓後執行 EXE。目標電腦不需要 Python、pip 或下載模型。實際相容 Windows 版本、DLL 需求及混合 DPI 必須由對應 EXE 的驗收紀錄證實。

## 目前實作

- 框選 OCR、截圖、歷史、搜尋、編輯、標籤及 TXT／CSV／JSON／ZIP 匯出。
- RapidOCR ONNX Runtime 1.4.4，內含並於載入前校驗三個模型；模型 SHA-256 與 v1.6.1 實際使用的模型一致。沒有在此次修復更換 v5／v6 模型。
- 二次辨識以重疊區域仲裁；同行文字依字類與幾何間距合併。繁中、稀有字與小字仍可能誤辨，信心分數不代表文字完整性。
- 新設定預設不監聽剪貼簿。啟用後可能保存密碼、Token、個資；資料庫、截圖、缩圖與匯出均為明文。**沒有自動保留期限清理**，需手動刪除並清空回收桶。升級既有設定會保留使用者原先的監聽選擇。
- 截圖／OCR 工作依序停止，資料庫確認寫入後才依 `capture.save_raw_image` 決定刪除成功辨識的原圖；失敗及空結果保留原圖供重試。

PaddleOCR／CnOCR adapter 保留作為實驗程式碼，**此候選應用不提供切換**。在別處 `pip install paddleocr` 不會替既有 one-file EXE 增加套件。佈景／字級及剪貼簿圖片收錄尚未完成，設定介面已停用對應控制項。`dictionaries/custom_tw_corrections.json` 未接入 Runtime，不宣稱已有台灣詞庫。

## 開發及驗證

使用 Python 3.12：

```shell
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python src/main.py --self-test selftest.json
```

Linux 只作測試與量測；完整桌面啟動、熱鍵及擷取以 Windows 為目標。Qt 無桌面測試時設定 `QT_QPA_PLATFORM=offscreen`。自我檢查會實際載入模型、辨識已知文字、開啟 Qt 及讀寫 SQLite；它不等於完整使用者流程或乾淨機器驗收。

Windows 建置：`python scripts/build.py`。輸出位於 `artifacts/`，詳見 [PACKAGING_NOTES](PACKAGING_NOTES.md)。首次與後續啟動均須在離線環境測試，不能只測已有模型快取的開發機。

## 已知交付阻擋項目

- Windows mixed DPI、多螢幕與休眠／RDP／Explorer 重啟尚未實測；現有框選仍使用 primary screen，不能宣稱已支援所有螢幕配置。
- 放大預估超過 1600 萬像素時明確失敗並保留原圖，避免失控分配。這是資源保護，尚未完成長條圖片切片辨識。
- 新空格／融合策略仍需擴充旋轉文字、表格及現場文件 Ground Truth；不宣稱整體 CER 已改善。
- 歷史自動歸檔／清除、完整設定矩陣、台灣 domain lexicon、optional engine native 整合仍有未完成項目。
- 穩定版 Release 需通過 [驗收清單](SMOKE_TEST_CHECKLIST.md)，本分支不自動發佈。

對未知 Microsoft 連線：已加入 ORT 啟動前 opt-out 與 session 前 `disable_telemetry_events()`；Linux 推論在 syscall 網路阻斷下測試。先前連線的實際 payload 仍 UNKNOWN，不能據此宣稱已確認沒有資料外傳。Windows 成品仍需依程序與目的地記錄網路事件。
