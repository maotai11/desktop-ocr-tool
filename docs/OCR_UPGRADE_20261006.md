# PP-OCRv6 升級與小範圍辨識修復紀錄

日期：2026-10-06。版本：1.7.0-rc.1 候選。此報告分開記錄原生模型參照、產品原始碼、Windows 成品及乾淨機器驗收，不互相替代。

## 最終模型選擇

產品提供 v6-small 與 v6-medium 兩組。兩組均使用 PP-OCRv6 Small detector；recognizer 分別為 PP-OCRv6 Small／Medium。原方向分類器只處理 0／180 度，並非 v4 文字辨識 fallback。舊 v4 detector／recognizer 不打入可執行成品。

RapidOCR 包裝層仍是 rapidocr-onnxruntime 1.4.4，ORT 1.30.0；這不代表權重仍是 v4。相同新權重與相同前處理，在 legacy／modern RapidOCR 3.9.2 的 34 張圖片各兩次配對中，文字、狀態、框數及座標全部相同；最大 confidence 差約 0.000006。故此次保留已整合的 tuple API，不同時更換更多包裝層依賴。

兩組新模型字表相同：18,708 原始字符，加 CTC blank／space 後 18,710 類。完整有序字表與 decoder 均經 SHA256 檢查。龘、尞、𠮷、𠀋、丨仍不在此字表；此次不增加字表或訓練模型，不用替換規則猜答案。

官方來源：
- [PaddleOCR 3.7.0／PP-OCRv6 發布](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0)
- [RapidAI 3.9.2 固定模型清單與 SHA256](https://raw.githubusercontent.com/RapidAI/RapidOCR/v3.9.2/python/rapidocr/default_models.yaml)
- [PaddlePaddle v6 Small recognizer 模型卡與 Apache-2.0](https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec)
- [PaddlePaddle v6 Medium recognizer 模型卡](https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec)

實際下載位元組 hash 與 publisher manifest 一致。ONNX 轉換來自 RapidAI 官方模型庫；它不與 PaddlePaddle 另一份 ONNX export 混用。模型授權来源與 code license 分開記錄；完整相依套件再散布／簽章驗收仍未全部完成。

## 案例名稱 → 檢查內容 → 實際發現

### 小範圍高筆畫

檢查：27 張固定像素，3 種內容 × 12／16／24px × 0／4／12px 留白，保留 literal 空白、標點及大小寫。

發現：舊前處理搭配 v4 有 84 edits／225 GT 字，僅 3/27 整句正確；先換 v6 recognizer 的混搭有 38 edits、15/27。再限制小 ROI 放大並補背景，混搭及完整 v6 Small 均達此批 27/27。這是調整策略所使用的 development set，不能稱為 holdout。

修正：短邊 <128、長邊 ≤512 的 ROI 最多放大 3 倍，補足 detector 空間，不再把小圖無限放大再縮小；原圖不改，保留座標轉換與處理來源。

### 新字型、新內容、深底與框線

檢查：另凍結 38 張未拿來調參的圖片，32 文字案例共 262 字，6 個負例。使用 Noto Serif、新字串、深底、表格框、單字及尺寸邊界。

發現：完整 v6 Small 為 28/32 文字案例 exact、4 edits（3 個漢字替換、1 個空白）；Medium 為 27/32 exact、5 edits（均為空白）。Medium 可改善部分複雜字，但不代表每一圖都更準。6 個負例在最終修正後均無文字。

這是有限 synthetic holdout，尚無原始使用者畫面像素的盲測準確率。拍螢幕照片有網紋、透視及額外縮放，不能當成原 OCR 輸入的重現。

### 空框被讀成「一」

檢查：180×40、白底黑色 1px 外框，另加一／二／十、框內文字、口／囗／回與深色反相等對抗案例。

發現：原 edge-median 選黑色作補邊，形成大黑畫布；新 detector 可把整張畫布讀成「一」，confidence 約 0.99。不是低信心問題。

第一次修法允許 75% 同色內部，造成框內真字的 correct→wrong 回歸，已拒絕。最終修法僅在完整外邊界逐像素同色、內部全部像素逐像素同色且對比足夠時，改「新增補邊」的背景色。沒有刪除原圖框線或按輸出字串過濾「一」。

容差版也被進一步收緊為逐像素完全同色，1／3／8／9／32 階低對比筆畫均不觸發空框覆寫；122 張實際補邊像素與已測版逐 byte 相同。最終兩個新模型各跑 122 圖 × 2 次；與相同輸入的舊補邊配對，50 個筆畫／方框控制及 38 張 holdout 未新增 correct→wrong 回歸，10 個負例全空。既有單筆畫／框內字的模型錯誤仍保留：46 文字控制中 Small 26 exact、Medium 25 exact，不能把「沒有新增回歸」寫成「所有控制都正確」。

### 1／2／3 次辨識與待確認

檢查：相同 38 張圖，各設總次數上限 1／2／3，記錄實際執行次數、錯誤及時間。空白／高信心結果不盲目重複；完全相同的處理像素也不重跑。

發現：此批增加次數沒有增加正確句數，反而增加延遲；因此預設上限 2，不預設 3。詳細最終數字與重現命令见 [retry report](OCR_RETRY_REVIEW.md)。

衝突候選保留來源，不靠跨模型信心直接排名。編輯視窗可比對／套用／復原，再儲存；人工改文與 OCR attempt 分開。待確認不自動複製，遲到／刪除／新版 job 不覆寫舊編輯確認。

### 原生 PaddleOCR 參照

檢查：獨立環境使用 PaddleOCR 3.7.0、PaddlePaddle 3.2.0、PaddleX 3.7.2，CPU、官方本地模型目錄，於 import 前施加程序層 kernel network block。

發現：真實 PaddleStaticRunner 推論成功，沒有載入 ONNX Runtime；三張同像素圖可辨識但仍有空格／形近字／罕字錯誤。原生模型與 ONNX metadata 的 18,708 字順序完全一致。現有未啟用的 native adapter 沒明確 model_name，會與新版預設 medium 名稱衝突；它仍不作成品切換選项。

可確認的是原生模型可離線執行，不能以此宣稱 Windows DLL closure 完成。原生參照原始證據保留於本機；此儲存庫不追加大量測試圖片。

## 模型設定、封裝與資源

- 模型選單真的寫入設定，下次啟動載入相應 detector／recognizer；測過 Small→Medium→Small 及取消不儲存
- 缺檔、SHA256／字表／輸出類別不符明確失敗，沒有回退 v4 的可執行選項
- 每次只推論一個 recognition crop；normalized width≤4096，單 job 累積≤65536（跨 tiles／重試共享）
- 因每個 crop 至少使用寬 320，最多約 204 個最短 crop，不能把名義 512-crop 另上限解讀為能處理 512 框
- 僅打包白名單模型、設定與三張逐字 fixture；不把 vendor 預設 v4 模型或實驗權重順便打包
- 已真實測過移走 vendor model 目錄仍能載入兩組 v6 並通過繁中／金額／日期/API fixture
- ZIP 綁定 commit／source tree／EXE hash／model hashes；檢查來源與 frozen self-test，以及真正 Qt app 的啟動／停止

## 驗證狀態

Linux source suite、離線 source/self-test、500 次負載與 Windows 成品結果以對應最終 commit 的報告為準；中間混搭量測不冒充最終完整 v6 成品測試。

首次完整 Windows 候選已通過：563 tests passed／2 POSIX-only skips，雙模型各 500 次 frozen 推論、離線 probes、精確 payload 白名單及 ZIP/hash 檢查，見 [驗證摘要](validation/v1.7.0-rc.1-prepublication.json)。Release marker 將再建置並檢查同一 run 的成品。

目前仍不能宣稱：乾淨 Windows 無預裝 DLL／Python／模型快取驗收完成、混合 DPI 全支援、原生推論永不返回時能限時關閉、全部文字正確、所有網路外傳已被證明為零。

私人收據、螢幕照片、聯絡資料、使用者工作文件均未放入此 repository 或 CI。
