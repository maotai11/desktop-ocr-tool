# Performance / Memory Forensics

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## Measurement design / attribution

Real native OCR、fixedmodelhash與GT、perf_counterwall latency、psutilCPU/RSS/threads每10ms、ru_maxrss。PID namespace 的os.getpid()不能直接查本環境host-mounted /proc；早期profile讀到另一process，已修正使用/proc/self Tgid。**早期baseline-progress/longevity等未完成logs不作結論**；已完成資料在named subdirs、trace與freeze可重跑。未捕捉完整allocatorcallstack，不能將steadyRSS判作leak/no-leak。

主A/B run_remaining序列執行，各ORT pool2threads是實驗控制，非現行appdefault；default測另有production-default。Linuxsharedhost不是專用benchmark機，latency不是Windows保證。模型load包含warmdummy，但不包含整個Windows啟動；firstOCR定義為load後第一GTinput。nativePaddle資料只有ru_maxrss與全pipeline36latency，fallback routing在app目前adapter損坏，故不能假造有效fallbackpeak。

| Workload | N | load ms | first OCR ms | p50 ms | p95 ms | p99 ms | sample peak RSS MiB | end RSS MiB | End native threads |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| controlled-v4 | 36 | 1790.6 | 1863.1 | 1334.0 | 2205.583 | 2660.521 | 997.2 | 231.9 | 19 |
| production-default | 3 | 2458.1 | 1506.3 | 2210.6 | UNKNOWN (N=3) | UNKNOWN (N=3) | 977.8 | 232.2 | 40 |
| native-scale-v5 | 36 | 687.4 | 109.3 | 94.9 | 155.340 | 181.797 | 315.9 | 276.1 | 19 |
| aligned-v6 | 36 | 568.8 | 3059.2 | 2424.3 | 3548.782 | 4325.653 | 1063.6 | 304.4 | 19 |
| second-pass | 12 | 2727.2 | 2427.4 | 1696.7 | UNKNOWN (N=12) | UNKNOWN (N=12) | 1059.1 | 277.7 | 19 |
| longevity-500 | 500 | 1526.0 | 370.5 | 251.1 | 292.555 | 406.890 | 488.1 | 226.4 | 19 |

500次load固定960×960白底含24pxTC行，secondpass=false、secondaryNull、2threads：第100次RSS 224.14MiB，第500次 226.14MiB；最後100次range 225.84–226.14MiB。未見這個workload持續大幅上升；不排除distinctimagecache、secondaryload、GUIQPixmap或10000次慢leak。500metrics的p95/p99只是此固定workload經驗值。

## Shape / second-pass probes

各freshsubprocess，realmodel，圖上10px「台灣稅務發票辨識」，nativeinfer而不是只有resize。8GiBaddressspacelimit、90stimeout；每個完成exit0，limit未被結果error觸發。4K結果done但空文字＝此GT小字漏掉，不能說done就是成功。

| Input h×w | OCR ms | peak RSS MiB | end RSS MiB | Result / interpretation |
|---|---:|---:|---:|---|
| [30, 2000] (30x2000) | 796.4 | 753.9 | 190.1 | needs_review / 臺灣稅務發票拼 |
| [50, 1000] (50x1000) | 696.6 | 610.9 | 169.9 | needs_review / 臺灣稅務發票辯藏 |
| [100, 3000] (100x3000) | 742.0 | 643.8 | 177.2 | needs_review / 臺海務登票進 |
| [20, 500] (20x500) | 707.0 | 630.1 | 176.1 | needs_review / 臺灣稅務發票辯潢 |
| [2160, 3840] (4k) | 1319.4 | 579.7 | 238.3 | done / 空文字 |
| [30, 180] (tiny) | 2343.4 | 634.0 | 167.1 | needs_review / 臺灣稅務發票辨識 |
| [40, 260] (secondpass) | 7128.9 | 915.8 | 242.6 | needs_review / 臺灣稅務發票辯識 臺灣稅務發票辯藏 |

secondpass專門probe把reviewthreshold設1.01強制兩次，這是測融合/peak的故障放大實驗，不是產品可選值或平均成本；結果兩個相同位置字串，印證O02。正常second-pass12樣本資料與firstpass比較不可用整體36平均對比；要同樣本配對。

## Resize allocation

| h×w | threshold | output h×w | ndarray MiB | resize wall ms | CPU s |
|---|---:|---|---:|---:|---:|
| [30, 2000] | 960 | [960, 64000] | 175.78 | 66.16 | 0.44 |
| [50, 1000] | 960 | [960, 19200] | 52.73 | 9.46 | 0.07 |
| [100, 3000] | 960 | [960, 28800] | 79.10 | 15.65 | 0.11 |
| [20, 500] | 960 | [960, 24000] | 65.92 | 9.55 | 0.06 |
| [2160, 3840] | 960 | [1920, 3413] | 18.75 | 18.02 | 0.14 |
| [30, 2000] | 1280 | [1280, 85333] | 312.50 | 93.31 | 0.62 |
| [50, 1000] | 1280 | [1280, 25600] | 93.75 | 16.09 | 0.11 |
| [100, 3000] | 1280 | [1280, 38400] | 140.62 | 29.21 | 0.20 |
| [20, 500] | 1280 | [1280, 32000] | 117.19 | 21.75 | 0.16 |
| [2160, 3840] | 1280 | [1920, 3413] | 18.75 | 18.38 | 0.14 |

CONFIRMED：max_long/max_pixel/scale_cap必要性已有allocation与CPU/RSS證據，但cap值仍待CER/detection對照。30×2000輸入180KB，960輸出184MB，1280輸出328MB；這不是每次只多一張小copy。MSS→numpyBGRA→BGRview→PILRGB→PNG→cv2decode→resize→detfloatbatch等跨階段可能同時存在；只看PythonGC不代表peak降低。4K2160短邊downscale1920，10px字筆畫濾縮後加vendor再縮，scenario空done；downscale責任比例需original/disable-scale/stage-only配對，不能將全部漏字歸AREA。

## UI / QObject memory

| Scenario | Count | retained objects | RSS MiB |
|---|---:|---|---:|
| settings_no_tag | 1 | 1 dialogs/editor；QObject 215 | 322.9 |
| settings_no_tag | 100 | 100 dialogs/editor；QObject 16847 | 624.7 |
| settings_no_tag | 500 | 500 dialogs/editor；QObject 84047 | 1843.7 |
| different_item_editors | 1 | 1 dialogs/editor；QObject 未count | 1845.8 |
| different_item_editors | 100 | 100 dialogs/editor；QObject 未count | 2034.2 |

Settings測的是no-tag診斷路徑（正式tagroute目前F01先NameError），此條件下100→500比例與parent-owned QObjectcounts，確定retention。Editor100個不同item已關但hidden dict强引用；不是同一個MainWindow重開500次的假leak。RSS含先前Settings保留，因此editor以delta解讀，不能說單一editor用2GiB。QPixmap/QImage/cache、third-partyunreleasedmodels、threadleak未全驗；A/Bfreshprocess避免保留多engine假誤判。

## Clipboard / SQLite / WAL

storage1000fake-tokenevents全部save，壁鐘117.47ms（directfakeemit＋Db dispatch之harness條件，非Windows實際剪貼簿frequency）。1000/10000/10500rows與檔案尺寸：

| Rows | DB bytes | WAL bytes |
|---:|---:|---:|
| 1000 | 471040 | 4181832 |
| 10000 | 3923968 | 4194192 |
| 10500 | 4112384 | 4194192 |

WAL約4.2MB平臺顯示本測auto-checkpoint有效，不能報unboundedWALleak；資料10500超設定10000與aged0archive則證明policy未消費。dedup orphan raw/thumb另可成長，見D06。匯出ZIP同步main並累積txt_content、目前UI最大100000，完整p95/disk/ZIPdoublebasename碰撞壓測UNKNOWN。

## Startup / unresolved performance gates

Linuxactualapp含真UI/DB/worker，platformmutex/hotkeystubs、2threads：[{'event': 'widget.show', 'ms': 1974.0299850000156}, {'event': 'engine_ready', 'ms': 3414.3728649996774}]，exit0。尚未包括interpreter/Windowsdiskcoldstart；UIshow不是firstpaint保證。

| Requested metric | Evidence status / next test |
|---|---|
| Windows coldstartup / cleanEXE extraction / HDD vsSSD | UNKNOWN；無Windowshost與build，不拿Linuxmodel load替代 |
| primary load / first / warm /500 | CONFIRMED於上述限定workload |
| p50/95/99 tail for real field mix | UNKNOWN；需足量heldoutimage分群/多coldruns |
| secondpass peak | CONFIRMEDspecialprobe；defaultdistribution由GT12sample可查 |
| fallback firstload/warm peak | nativePaddlev5 load4531ms/ru_maxrss約642MiB可查；appfallback兩adapterbug未修，不能冒充有效fallbackbenchmark |
| 4K/tiny/extreme | CONFIRMEDrealprobes，但只有單張/次，不可p99 |
| multiwindow/QObject | CONFIRMED500Settings/100editorretention；不同WindowshighDPItextures UNKNOWN |
| clipboard highfrequency | CONFIRMEDfakecontrolled1000dispatch；real Winclipboardbusy/ownership UNKNOWN |
| thread /model /QPixmapleak | UNKNOWN全面結論；500fixedprimary未見大幅增長不是全app無leak |
| profiler attribution | 有RSS/CPU/native-threadtime-series；allocatorstack/flamegraph UNKNOWN |

Fix gate：相同image與參數、模型hash、hardware/environment，before/afterp50/95/99、CPU/peakRSS/steady100/500及GTmetrics；至少3coldruns並記load/first/cacheconditions。未實作fix，沒有宣稱記憶體或速度已改善。
