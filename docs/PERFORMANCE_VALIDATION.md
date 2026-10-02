# 修復候選效能量測

CONFIRMED：Linux x86_64、Python 3.12.14、固定 Core dependency environment、真實三個 v4 ONNX 模型。推論之前 ORT opt-out，LD_PRELOAD 與 seccomp 拒絕 socket／connect／send。**本次沒有與相同硬體／負載的舊版重新配對，不能宣稱效能改善。**

使用 `scripts/benchmark_runtime.py`，160×160 生成數字圖「12345」，預設 resize／second-pass policy；500 次均符合文字 GT。20 ms 採樣，原始 trace、每次 latency、RSS 及 native thread checkpoints 見 [runtime-500.json](evidence/runtime-500.json)。這不是現場繁中 CER benchmark。

| 量測 | 本次結果 |
|---|---:|
| 模型載入含暖機 | 1.789 s |
| 首次圖像辨識 | 439.8 ms |
| warm p50 / p95 / p99 | 265.0 / 329.5 / 487.7 ms |
| 全程序採樣 Peak RSS | 418.65 MiB |
| 第 1 / 10 次 RSS | 192.08 / 213.09 MiB |
| 第 100 / 500 次 RSS | 213.23 / 213.88 MiB |
| 程序結束前 RSS | 213.88 MiB |
| 第 1 / 10 / 100 / 500 次 native threads | 13 / 13 / 13 / 13 |
| 累積程序 CPU time | 557.14 s |

限制：單一固定小圖、已有 OS cache；沒有測量 Windows one-file extraction／真正冷 OS-cache startup、4K 持續負载、多視窗／頻繁剪貼簿、fallback first-load 或壓力後 DB/WAL 增長。採樣可漏過短於 20 ms 的峰值。RSS 接近穩定不等於證明沒有 leak。

方法修正：最初 `psutil.Process()` 在此容器 PID namespace 回報 462848 bytes／1 thread，與實際推論程序不符；已中止並廢棄该次。正式數據用 Linux `/proc/self/status` 的 Tgid，Windows 用 os.getpid()；沒有把錯誤低值当成記憶體改善。

16M-pixel guard 在呼叫 cv2.resize 前檢查預估輸出像素。30×2000、50×1000、100×3000、20×500 均以 allocation spy 證明未進入超預算配置。這些案例現在回報 failed 並保留原圖，**不代表已完成長條圖片 OCR**；不得用這個 guard 取代有 GT 的 tiling／resize A/B gate。
