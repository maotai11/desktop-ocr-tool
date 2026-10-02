# Windows 離線交付候選

本次沿用既有 Core one-file 交付路徑；沒有選擇或自動部署 optional engine 架構。`scripts/build.py` 在 Windows 上執行 PyInstaller，將 Python、Qt、ORT、RapidOCR、三個經 hash 校驗的 ONNX 模型及必要資源封裝。Linux 不產出 Windows EXE。

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python src/main.py --self-test source-selftest.json
python scripts/build.py
```

輸出：`artifacts/DesktopOCRTool-v1.6.2-rc.1.zip`、SHA256、release 目錄及 `BUILD_MANIFEST.json`。環境變數 `DESKTOP_OCR_VERSION` 可覆寫版本，但不能替代版本驗收。解壓至一般使用者可寫入的資料夾；不要以系統管理員執行。設定、data、logs 儲存在 EXE 所在目錄，one-file 的 `_MEIPASS` 暫存只放解包後執行資源。

內含的 runtime hook 在 application import 前停用 ORT telemetry。模型校驗缺檔、manifest 缺漏或 hash 不符均拒絕載入。此 hash 可偵測意外損壞；同時修改 EXE 與 manifest 的攻擊仍需可信簽章／發佈來源驗證。

`--collect-*`、`--hidden-import` 不再包含同時被排除的 Paddle 套件。依賴版本固定在 requirements；尚未完成所有平台 wheel 的 hash lock 及授權清單驗收，不能宣稱 supply chain 已封閉。PyInstaller 可攜程式仍依賴 Windows 系統元件，不可把開發機成功當成乾淨電腦成功。

## 候選與正式版的界線

GitHub Actions 會在 Linux／Windows 執行測試，Windows 建置 EXE，並針對該 EXE 加入 outbound firewall block 後執行 `--self-test`。Runner 不是乾淨使用者電腦；此紀錄僅屬 build/integration gate。成品需另外在無 Python、無 pip、無 OCR 快取、斷網的 Windows VM，以一般使用者執行完整流程。記錄 OS build、CPU 架構、ZIP／EXE SHA256、測試結果及網路事件。

## Optional engine 架構尚待選擇

| 方案 | 必須交付及驗證 |
|---|---|
| Core | 目前固定 RapidOCR v4；先完成乾淨機器 gate |
| Full | 另包 Paddle native runtime、精確模型及授權；量測 EXE/RSS/冷啟動 |
| Plugin | 定義受驗證的獨立程序協定、版本與資產簽章；host pip 不是 plugin |
| Onedir | 可降低重複解包成本；需測完整資料夾搬移、DLL 路徑、更新原子性 |
| 另載引擎 | 在有網路的準備機下載並驗證完整離線包，搬入目標機；不可要求目標機首次連網 |

沒有選定模型遷移方案。Phase 2 的 v6 recognition 結果包含已知回退，須先通過模型 gate。

參考原始文件：
- https://www.pyinstaller.org/en/stable/operating-mode.html
- https://www.pyinstaller.org/en/stable/runtime-information.html
