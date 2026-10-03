# OCR 恢復修復驗證（2026-10-03）

基準為已推送的 `bba210ec71c7d08d0093c11dba3964d80d0dfb27`。先前聲稱 192 tests／v4-v6 tiling 的未推送工作沒有在此工作區取得；本輪是在已推送基準重新實作，**不是取回該份未保存程式**。三個 production ONNX 模型與雜湊不變，沒有換入 v6，也沒有用詞庫全域替換罕字。

## 本輪修正與可追溯資料

- 單一区域低信心即觸發 retry；不再讓大量高信心框掩蓋罕字所在的小框。任何弱框也令結果保持 `needs_review`。這仍不能偵測沒有框的漏字。
- 二次影像增強或推論出錯時保留可用第一次文字，附警告與 `needs_review`；第一次本來無結果且二次失敗仍回傳 `failed`。
- 融合面積使用框的聯集，避免重疊碎片重複計算 coverage。較短高信心候選不能覆蓋較完整結果；第二次實際擴大文字／框覆蓋時不因分數較低就丟棄。
- 重疊候選文字不同時保留第一次、第二次 raw hypotheses 與 `needs_review`。原始字串、zh-hant 轉換後字串、實際模型版本分開保存；沒有把轉換後文字冒充原始模型輸出。
- 行合併以各行局部中位字高／中心判斷，避免大標題讓兩個小字行合併。支援 NumPy quadrilateral／平面 bounds，CJK 相容字與 variation selector 邊界不增加多餘空格。
- 解碼前先檢查同一開啟檔案的大小、Pillow header 尺寸，再交給 `cv2.imdecode`；32 MB 壓縮檔與 3200 萬來源像素上限。無效 BGR／空輸入回傳明確失敗，並保留原始來源供上層重試。

`hypotheses` 的單次推論框位於 processed-image 座標；`coordinate_transform` 說明回原圖比例。`detail` 使用原圖座標。切片結果的 `preprocessing.tiles` 提供來源裁切位置、實際 resize、padding、scale 與 engine/model_version；每片原始 hypotheses 另行保留，融合後文字明記 `text_source=tile_fusion`。本輪資料層另以完整 result provenance 保存這些資料，不只保存 normalized detail。

## 有界切片與明確限制

新 recovery 路徑以來源 tile 逐片處理，2–3 倍放大、最大 1536×1536 inference tile、短軸 padding 至 736。對已安裝的 RapidOCR 1.4.4，這避開該路徑的 global max-side=2000 縮小，以及 detector min-side=736 再次放大。重疊至少為規劃 tile 邊長的 1/4，尾片可能有較多重疊。

- 單圖最多 64 tiles、所有規劃 inference tiles 累計 8000 萬像素；超限在 resize 前拒絕。限制是工作量／配置保護，**不是整個 ONNX Runtime process 的 RSS 上限**
- 完全單色 tile 可精確跳過推論；只要任一 channel 有 1 級像素差就不跳過，沒有低對比 threshold
- 支援一致的水平文字交界拼接與同位置包含片段去重；空白只用於幾何重複比對，保留下來的字串不做去空白正規化
- 模稜兩可的部分重疊保留候選並警告；所有 tiled output 皆 `needs_review`，沒有用高信心宣稱完整
- 任一實際推論 tile 失敗使整次 `failed`，不把缺少後半張圖的部分結果冒充成功
- 來源 longest-side ≤2000、aspect ratio ≤8 且舊 resize 預估 ≤1600 萬像素時，為相容性保留舊 whole-image 路徑。例如 1000×200 仍會先放大再由 vendor 限長邊；**沒有宣稱全產品已消除 hidden resize**
- 原本能大幅縮小處理的極大圖可能因新的來源／總工作量限制明確拒絕；這是安全取捨，不能稱為所有尺寸都已支援

旋轉字、直排文字、表格、複雜跨片行與真實低對比畫面尚無完整現場 gate。切片可能改变錯字及空白錯誤分布，原始候選與圖片仍需人工核對。

## 實際 v4 像素比較

腳本在推論前生成並凍結 7 張 Noto Sans CJK TC 合成圖與 GT，讀取 pinned baseline 的 engine/fusion/postprocessor 進行配對，使用同一個真實 v4 model session。詳細圖檔、GT、模型／字型／來源程式 SHA-256、逐框輸出、候選、座標與量測在 [evidence/ocr-recovery-20261003](evidence/ocr-recovery-20261003/)。

| 合成案例 | GT 字元 | 基準 edits | 修復 edits | 結果 |
|---|---:|---:|---:|---|
| ordinary_tw | 15 | 3 | 3 | 沒有改善 |
| ordinary_mixed | 40 | 0 | 0 | 保留 exact match |
| rare_tw | 15 | 12 | 12 | 罕字辨識仍明顯失敗 |
| long_strip，2000×30 | 32 | 32，整次 failed | 1 | 有結果但仍有 `臺→壹`，需 review |
| long_seam，2000×50 | 20 | 20，整次 failed | 3 | 仍少字／空格，需 review |
| small_large，2300×600／10px | 32 | 4 | 4 | error 總數不變，錯字／空白分布有變 |
| small_4k，3840×2160／10px | 32 | 17 | 4 | 只支持此合成圖的恢復，不是現場整體 CER |

GT 沒有按輸出改寫；edits 使用既有 NFC Levenshtein，不把全半形、空白或 `台／臺` 當成相同。罕字圖已查看可見像素，不過字型完整 codepoint 覆蓋沒有獨立 cmap 驗證，不能當字表 coverage benchmark。

開發初版曾在 `long_seam`／`small_large` 留下重複片段。根因為跨片框高漂移與片段空白不同，已加入實際幾何值的 reproduction tests 並重跑最終七圖；最終輸出仍保留其他錯字，沒有把「移除重複」寫成整段正確。

最終單次 4K 圖觀察：基準約 918 ms、修復約 1790 ms。初版對白色 tiles 做無用二次推論約 48 s，精確單色跳過後改善此案例成本。這是单次觀察，不是 p95/p99；同 process 共用 session，RSS 會受 allocator／前例影響，不能用本表宣稱降低峰值或沒有 leak。最終逐樣本 sampled RSS 最高約 985 MiB，仍需隔離 process、長跑與使用者硬體量測。

整合 review 另重現週期字串／帳號連續數字的 seam-loss：6 字元框各寬 60 px、相隔 40 px 時只有 2 字元重疊，舊的「最長可接受 suffix/prefix」卻會吞掉額外重複字。現在對所有 exact matches 計算幾何誤差，只在容許範圍內且最佳候選比次佳明確接近時拼接；歧義或文字 match 不受幾何支持時保留雙方並標 conflict。後續 duplicate fallback 亦要求真正的水平包含關係（容許有限框邊漂移），不能把 75% overlap 冒充 containment 再偷偷刪掉候選。新增 `ABABAB`、`000000`、`121212` 與 unique-but-wrong-geometry fixtures，七張固定 GT 圖再跑一次，inputs.json 與每張 PNG hash 完全不變。

既有 24 張 frozen-first-pass spacing replay 仍為 240/624 edits（38.46%），whitespace edits=27；沒有重新執行那 24 張原圖，也沒有宣稱該 corpus CER 改善。

## 驗證命令與結果

使用父任務建立的 Python 3.12 venv、exact `requirements-dev.txt`。環境設定：

```sh
export QT_QPA_PLATFORM=offscreen ORT_DISABLE_TELEMETRY=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
PY=/workspace/scratch/40b4883b300a/ocr-venv/bin/python
$PY -m pytest -q tests/test_ocr_regressions.py tests/test_ocr_preprocess.py tests/test_hardening.py tests/test_secondary_engine.py tests/test_h4_fallback_edge_cases.py
# 137 passed，包含 52 個新 deterministic OCR tests
$PY -m pytest -q
# 該次共享 checkout：295 passed in 9.92s；其他工作後續測試數以父任務最終紀錄為準
$PY -m ruff check --select E9,F63,F7,F82 src/ocr/engine.py src/ocr/preprocessor.py src/ocr/fusion.py src/ocr/postprocessor.py tests/test_ocr_regressions.py scripts/validate_ocr_recovery.py
# All checks passed；不是全量 ruff 清零宣稱
$PY scripts/validate_ocr_recovery.py --baseline-ref bba210ec --font /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc --font-index 3 --output-dir docs/evidence/ocr-recovery-20261003
$PY scripts/replay_spacing.py --baseline docs/evidence/spacing-replay-input.json --report docs/evidence/ocr-recovery-20261003/spacing-replay.json
```

本輪沒有硬性封鎖網路的 syscall／ETW 驗收，不能因 opt-out 環境變數就稱為 clean offline acceptance。既有 Phase 2 的 v4-det/v6-rec 318-crop benchmark 與 12 個 exact-match regressions 未重新取得／執行，仍是未過的換模型 gate。沒有 Windows packaged app、mixed DPI、旋轉／表格現場 holdout 或正式發佈驗收。
