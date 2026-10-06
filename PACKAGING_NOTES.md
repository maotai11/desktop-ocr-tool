# Windows 離線成品建置

版本：1.7.0-rc.1 候選。只能在 Windows 建置 Windows EXE；Linux 原始碼測試不能證明 Windows 成品可用。

## 準備與建置

在有網路的 Windows x64 建置機使用 Python 3.12：

```powershell
python -m pip install -r requirements-dev.txt
python scripts/prepare_models.py
python -m pytest -q
python src/main.py --self-test source-selftest.json
python src/main.py --smoke-app source-app-smoke.json
python scripts/build.py
```

prepare_models 只用在建置／開發階段，依 manifest 下載固定官方模型並驗證 SHA256 與大小。既有檔案 hash 不符會失敗，不擅自覆寫。目標電腦執行 EXE 時沒有下載步驟。

## 內含內容

- Python、Qt、ONNX Runtime、RapidOCR 程式與必要資源
- v6 Small detector、v6 Small／Medium recognizer、固定方向分類輔助模型
- 兩組模型 manifest、固定繁中／金額／日期/API 圖片與正解
- 模型版號、SHA256、有序字表及 tensor 契約驗證

使用明確的檔案白名單，沒有整包收進 vendor 的 v4 預設權重、ORT demo ONNX 或其他實驗模型。兩組文字辨識都用 v6，不會在錯誤時回退舊文字模型。

## 成品驗證

建置後先檢查實際 PyInstaller payload，再對同一 EXE 執行 OCR／SQLite 及 app lifecycle probes。三張固定圖片逐字比較，保留空格與大小寫；兩個 profile 都必須通過。缺少模型、Runtime metadata、字表不符、來源hash不符或多出未允許的 ONNX，均不能產生通過的候選。

GitHub Actions 另在 Windows 對該 EXE 設 outbound firewall block，重跑兩種 probes，核對 ZIP、EXE、source commit、model hashes 與同一份 test results。

輸出位於 artifacts：版本化 ZIP、EXE、BUILD_MANIFEST、SOURCE_MANIFEST、SHA256、候選驗證報告及授權說明。

解壓後可在一般使用者權限執行：

```powershell
.\Validate-Candidate.ps1 -BundleDirectory . -ReportDirectory .\candidate-check
```

這是候選一致性檢查，不是完整的乾淨機器或 UI 驗收。

## 尚未代替的驗收

Windows runner 已安裝的系統元件可能掩蓋 DLL 依賴。仍需真正無 Python／pip／模型快取、斷網、一般使用者的 Windows VM，測冷啟動／第二次啟動、中文含空白路徑、框選、編輯、候選確認、匯出及退出。

混合 DPI、多螢幕、RDP／休眠及 native inference 永久不返回時的程序隔離仍未完成。NOT_RUN 項目必須留在報告，不得因 CI 成功就改成完成。

發佈 job 只接受此 repository、fix/ocr-model-upgrade 分支及精確的 publish-prerelease: v1.7.0-rc.1 commit marker，並只取同一 run 通過驗證的成品。普通 push／PR 不會發佈。
