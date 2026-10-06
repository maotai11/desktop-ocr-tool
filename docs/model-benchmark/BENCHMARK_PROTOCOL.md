# 第二階段：模型候選配對驗證規格

審計與候選實驗皆基於 `maotai11/desktop-ocr-tool@f5af545cabc4e9a649cd57497fbf5bf42f2ff138`。這一階段修改的範圍只有 benchmark 與證據文件；沒有 product code、production config 或模型替換。

## 預先固定的資料

- 公開文件：財政部營業稅 401 表單、電子發票列印格式範例（包含影像式收據與向量文字版）、健保署執行業務收入補充保險費表單。來源及 SHA256 在 `evidence/source-downloads.json`。
- 74 個標註欄位 × 96／144／192 DPI＝222 個 recognition crops；公開來源中的範例識別碼不是使用者客戶資料。
- 6 個合成領域 × Sans／Serif × 8／10／14／24px × clean／lowcontrast_blur＝96 個 crops。領域為會計、稅務、複雜字、罕字、正確上下文負例及中英數字混排。
- 合計 318 個 recognition crops；41 個整體流程輸入包含 12 張公開文件、24 張合成小字圖及 5 個極端尺寸／4K 圖。沒有按照輸出結果挑選測例。
- 全部合成字元經 font cmap 檢查，兩種字型都沒有 missing glyph。GT 明確區分 PDF 文字／座標與人工轉錄；公開欄位 contact sheets 已逐欄視覺核對，沒有第二位獨立人工標註者。

公開表單渲染可以反映正式排版和字型，不能替代客戶實際掃描件、拍照件或螢幕截圖。不同 DPI 仍是相同欄位的相關變體。整頁只標選定欄位，故不能宣稱全頁 detection precision 已測。

## 四個候選及控制條件

| 代號 | Detection | Recognition | 額外差異 |
|---|---|---|---|
| v4 | legacy PP-OCRv4 | legacy PP-OCRv4 | rapidocr-onnxruntime 1.4.4 |
| v5 | PP-OCRv5 mobile | PP-OCRv5 mobile | rapidocr 3.9.2；使用 v5 normalization、max-side detector strategy |
| v6 | PP-OCRv6 small | PP-OCRv6 small | rapidocr 3.9.2 預設 detector normalization／min-side strategy |
| v4det-v6rec | 與基線相同 SHA256 的 v4 detector | 與 v6 相同 SHA256 的 v6 recognizer | modern API；用以評估保留 detection weights 的候選，不宣稱只有一個變數 |

每個候選單獨 process、依序推論。ORT inter/intra pools 皆 2 threads，OpenCV 2 threads，OpenBLAS／OMP 2 threads。CPU affinity、實際版本、有效 config、已載入 session path／SHA256／字表留存。

共同 app boundary：原始 BGR → 現行 `upscale_if_small(..., 960)` → vendor detection／classification／recognition → 產品 `zh-hant` 與 `sort_boxes_and_merge()`。每張圖重複 2 次；第一張第一次包含首次 inference，模型 construction 時間另記。此實驗固定 first pass，沒有 second-pass／secondary fallback；不冒充完整產品預設配置。

三個 recognizer 直接讀相同 GT crops，bypass Detection；輸出 raw 和 converted。Hybrid 的 recognizer 仍實跑，檢查其同 crop 是否與 v6 一致。不能把 full-pipeline 改善完全歸因 recognition weights。

另對 5 個 shape 輸入跑 bounded 實驗：目標沿 app 縮放倍率，但同時限制 scale≤3、long side≤2560、pixels≤4,000,000。這些是待驗證的實驗值，未導入產品；只有 memory 降低而 CER／recall 下降，不視為修正成功。

## 指標定義

| 指標 | 計算與限制 |
|---|---|
| Recognition CER | 固定 GT crop，NFC後 Levenshtein (S+D+I)/N；分 raw／zh-hant |
| Miss／Substitution／Insertion | 分別 D/N、S/N、I/N；recognition miss 不等同 detection miss |
| Recognition exact | 整個 crop 字串精確相等，包括標點與空格 |
| Whitespace Error Rate | 完整字串最短 edit alignment 中涉及空白的 edit/N；不採 whitespace-only stream |
| Detection IoU≥0.5 recall | detector-only polygons map 回原圖；selected GT boxes 與 detections 做一對一 Hungarian IoU assignment；匹配 IoU≥0.5 的 target/全部 target |
| Union coverage≥0.8 | 所有 detection polygon 聯集覆蓋 GT box 面積≥80%；用來分辨 segmentation 與完全漏偵測，不替代 IoU recall |
| Pipeline selected-field CER | 對 final retained boxes 另做同樣一對一 matching；IoU<0.1 視為空結果；屬欄位取回診斷，不是完整頁面閱讀順序 CER |
| Synthetic pipeline CER | 因整圖只有一行完整 GT，可直接比較產品 merged_text |
| Latency | perf_counter，涵蓋 app resize＋vendor pipeline＋產品文字合併；stage-only probe 時間另列 |
| RSS／CPU／threads | 10ms psutil sample，native thread counts；ru_maxrss 補核，source inputs 載入與stage probes都可能增加 high-water mark |
| Paired bootstrap | 公開欄位按原始 field 分 cluster；同 field 的 DPI 不當獨立樣本。合成按 font/domain 分 cluster。只描述此有限 corpus，非代表性母體保證 |

為防文件 metadata 干擾，GT／預測皆不做 NFKC、不刪標點、不強制台／臺或人名變體等價。Whitespace-insensitive CER（若另外列示）只作誤差分解，禁止成為產品全域移除空白的政策。

## 網路與完整性

任何 ORT import 前設定 `ORT_DISABLE_TELEMETRY=1`，Linux x86_64 seccomp 阻斷 socket/connect/send、io_uring、x32；LD_PRELOAD 記錄 libc 網路 attempts。直接 syscall guard test 須全為 EPERM。此證據限定新啟動 benchmark process，不涵蓋 Windows ETW，也不回推第一階段未知 payload。

套件／模型／公開資料下載與 inference 分開。只在準備步驟存取已明示的公開 endpoints；推論一律離線。資料集 frozen manifest SHA256、模型 SHA256 和產品 git status 在分析前／交付前核對。

## 決策門檻

1. 先確認模型確實已載入、GT crop 一致、網路被阻斷，才採信 CER。
2. 候選須在公開表單及合成挑戰分別改善；任何罕字、會計金額、日期、百分比或正確文字回歸需列明，不能由平均值掩蓋。
3. 只改善 recognizer 而 detector 回歸，應評估保留 detector／調整 detection preprocessing 的候選；仍需同條件配對證明。
4. p95／RSS需連同形狀、完整pipeline和硬體條件解讀；兩次重複不是可靠 p99 尾延遲驗收。
5. 領先候選只能進入 integration branch 的下一階段；升級發布仍需修復 provenance、fusion、spacing、config routing、lifecycle 等已確認問題，再過 Windows clean-machine、離線啟動與真實客戶 corpus gates。

## 執行中的方法修訂（保留失敗證據）

v5 第一輪在 shape-30x2000 拋出 ResizeImgError，已保留於 v5-app-aborted 與 log。harness 增加每張圖的 exception ledger，繼續其餘測例；沒有修改模型、推論參數或 GT。失敗圖計入失敗率／漏字，成功 latency 與失敗 latency 分開，禁止把快速失敗混成速度改善。v4已完成的同算法證據保留，兩版 harness SHA256 在 harness-history.json。run-status 中 process exit0只表示程式執行完成，仍須看每張 errors；不是模型全部通過。

## 探索性追加：保留 legacy runtime，只換 rec 權重

在確認 modern API 與產品 tuple 解包不相容後，追加 `legacy-v4det-v6rec`。Legacy1.4.4 的 `rec_model_path` 是真實支援參數；以已下載、固定SHA256的v6small rec ONNX傳入，保留legacy v4 det／cls。使用同318裁切與41輸入，非根據結果篩選樣本。這是探索性候選，不包含在原先四組預先規格的統計假設內；成功載入還不等於全模型／版本支援，必須核對字表和模型 SHA256、輸出、latency、memory及return contract。

## 回歸分類

回歸案例保留原始 GT、v4 與候選全文。全形／半形標點的變化也計入 literal CER；分析時可標註 NFKC 後才等價的情形，避免把它和金額數字、中文語意變更混成同一種影響。這個分類不改主指標，也不代表自動正規化對帳號、公司登記名稱或會計格式一定可接受。

## RSS 配對範圍修正

cap 的主要 RSS 對照使用 `*-app-shapes` 與 `*-bounded`：兩者皆 fresh process、相同五個 shape、相同順序與兩次 OCR。整體流程 `*-app` 先跑過表單，其 resident allocations 不作 cap 因果比較。保留其整體 peak作負載描述；禁止以該值宣稱 cap 單獨降低某比例。每個process只有一次冷啟動，任何p95/p99都不是release級尾端延遲保證。
