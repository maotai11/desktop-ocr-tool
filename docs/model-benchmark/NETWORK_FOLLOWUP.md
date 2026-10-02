# Microsoft 連線與第二階段網路隔離追蹤

基準 HEAD：`f5af545cabc4e9a649cd57497fbf5bf42f2ff138`。日期：2026-10-02。此文件延續第一階段 SECURITY_PRIVACY_AUDIT.md 的 S02；沒有忽略先前的未知 payload，也沒有擅自更改產品或 Windows 防火牆。

## 結論的邊界

| 問題 | 狀態 | 證據／處理 |
|---|---|---|
| 第一階段載入的 ORT1.30.0 含 Microsoft POSIX 1DS telemetry 初始化路徑 | CONFIRMED | 第一階段 pinned 官方 source、binary 字串與 opt-out 對照 |
| 先前實際送達的 payload、是否包含 OCR 文字／截圖 | UNKNOWN | 沒有 wire capture；不能把 auto-review 拒絕當成已送達，也不能說確定沒送 |
| 本輪 ORT 初始化前 opt-out | CONFIRMED | runner 設定 ORT_DISABLE_TELEMETRY=1，harness 在 import 前 assert；disable_telemetry_events 為補充 |
| 本輪 Linux OCR process 的 socket／connect／send 系統呼叫阻擋 | CONFIRMED | seccomp＋LD_PRELOAD；直接 syscall fixture 全部 EPERM；每個推論 log 含 barrier 紀錄 |
| 本輪 modern import 的 AF_INET6 attempt 來源 | CONFIRMED | Python audit stack 指向 urllib3 的 _has_ipv6("::1")；socket 建立被拒 |
| 產品 master／Windows ETW 已修復或無連線 | UNKNOWN／尚未修改 | 本輪只隔離 benchmark；不能外推到產品發布 |

## 新 socket attempt 的真實 Call Path

`from rapidocr import RapidOCR`
→ lazy `rapidocr.__getattr__`
→ `rapidocr/main.py`
→ `cal_rec_boxes`
→ `ch_ppocr_rec/main.py`
→ `utils/download_file.py` import requests
→ requests import urllib3
→ `urllib3/util/connection.py:137` 的 `HAS_IPV6 = _has_ipv6("::1")`
→ line126 `socket.socket(socket.AF_INET6)`
→ guard 拒絕，errno=EPERM。

`_has_ipv6` 的原始碼下一步是對本機 IPv6 loopback `::1`、port0做bind，確認平台是否可用；本輪在 socket 建立就被阻擋，沒有走到 bind。這個具體事件沒有 Microsoft endpoint、HTTP request 或 payload。不能把 AF_INET6 建立意圖直接稱為上傳資料，也不能據此替所有未來第三方網路行為背書。

證據：`evidence/network-origin.json` 保存完整 stack；`network-origin.log` 保存 guard 訊息；`source-snapshots/vendor-connection.py` 保存對應 source。這個 probe 只解析 class 的 import，不建立模型 session；沒有放行網路來追查。最初只做 `import rapidocr` 因 lazy namespace 未觸發 import，因此零事件；修正 probe 後重現。方法修訂留在 `network-origin-method.json`，不把第一次零事件當無網路的證明。

## Microsoft 初始化根因與本輪控制

第一階段追到的來源是 ORT native POSIX telemetry，而非這次 urllib3 的本機能力檢查。現有依賴下限可 resolve 到含該路徑的 ORT1.30.0；product master 沒有在所有 ORT import 前建立 opt-out。第一階段已核對官方 `PosixTelemetry::Initialize`：先檢查環境變數，再決定 uploader／identity 初始化；在 session 建立後才呼叫 disable API，不能倒推初始化完全沒有發生。

本輪把公開依賴／模型／PDF 的下載準備與 OCR 推論分開。模型已按 SHA256 預置，所有候選與 API probe 都在不可連網條件下完成。Linux x86_64 syscall fixture 覆蓋 socket、connect、sendto、sendmsg、sendmmsg、io_uring及x32 socket；LD_PRELOAD另記錄 libc socket與DNS attempt。這些是本次執行條件的證據，不是桌面產品通用隔離沙箱。

第一階段官方來源：

- [ORT v1.30.0 Privacy](https://github.com/microsoft/onnxruntime/blob/v1.30.0/docs/Privacy.md)
- [ORT v1.30.0 POSIX telemetry source](https://github.com/microsoft/onnxruntime/blob/v1.30.0/onnxruntime/core/platform/posix/telemetry.cc)

這裡保留第一階段已取得的版本限定證據，沒有把未取得的封包內容補寫成結論。原始 Linux ptrace／strace 不可用的限制也未消失。

## 產品修正的具體範圍與 gate

沿用 S02 的 Medium severity；本輪沒有證明使用者資料外洩，不提升成 Critical。

- **位置／File／Class／Function：** `src/ocr/engine.py` 的 ORT／RapidOCR 初始化、`src/main.py` 啟動邊界、PyInstaller frozen startup；native `PosixTelemetry::Initialize`。
- **真實 Call Path：** app startup／engine first-load → dependency import／ORT environment → native telemetry 初始化。必須涵蓋 primary與secondary首次載入，不能只在 UI 開關後才處理。
- **根因：** 依賴版本 resolve 與 native 初始化政策未受產品啟動契約控制；「離線 OCR」文件描述不是 network policy。
- **重現：** 固定 wheel／模型 hash，空白 canary／公開 GT，冷啟動觀察；本輪以禁止連網方式驗證 OCR 可完成。不得為取得 payload 任意允許敏感資料 process 出網。
- **使用者影響：** 使用者無法從 UI／README 得知第三方初始化可能觸發的連線意圖。
- **資料完整性影響：** 此問題沒有證明 OCR DB 被修改；先前傳輸內容仍 UNKNOWN。
- **效能影響：** telemetry 初始化可能有額外工作，但本輪未分離量測其 CPU／latency，不捏造數字。
- **資安／隱私影響：** 本次 inference 已阻擋出網；不能據此宣稱 Windows、原有 EXE 或之前的執行安全。
- **修正方向：** 在所有 native ORT 初始化前設定與驗證政策，固定依賴版本／hash、預置模型；將必要的公開模型下載与OCR處理明確分開。若選擇其他 ORT build，亦需證明其實際 binary 行為。
- **修正驗證：** source startup、frozen EXE、secondary cold-load、錯誤復原分別跑 Windows network／ETW gate；清除既有cache後驗證離線模型可用，記 endpoint／payload schema／consent policy，確認沒有無聲回退下載。
- **Regression：** 過晚opt-out無法覆蓋初始化；缺模型時全面阻擋可能令功能不可用。需將模型缺失顯示為具體錯誤，不能自動繞開離線條件。

此修正尚未套用到 repository。下一階段若進行實作，需與模型更新分別驗收，不能用辨識 CER 改善作為網路問題已修好的證據。
