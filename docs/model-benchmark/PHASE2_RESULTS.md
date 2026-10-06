# 第二階段 OCR／效能配對結果

審計基準：`maotai11/desktop-ocr-tool@f5af545cabc4e9a649cd57497fbf5bf42f2ff138`。日期：2026-10-02。產品原始碼、設定與正式模型未變更。CONFIRMED＝本批程式／實測證據；INFERRED＝候選解釋；UNKNOWN＝仍未驗證。

本階段完成五個候選各 318 次固定 crop recognition、41 個 first-pass 圖像×2次 pipeline、每圖另外一次 detector-only probe；共 1,590 個配對 recognition 結果、410 次主要 pipeline 呼叫及 205 次 detection 呼叫（包括 ledger 中失敗）。另外四候選各跑 app/bounded 五種尺寸的 fresh-process 對照。初始 aborted run、smoke 與失敗重跑不混入主統計。

## 資料與方法界線

GT manifest SHA256：`740d843a7b9fdd393af52f83c37b7e9614e7b3f0566d91cc58768c003d5c30cd`。公開資料來自財政部401表、電子發票格式範例、健保署補充保險費表；網址及 PDF SHA256 在 evidence/source-downloads.json。公開 74 欄×3DPI；合成6領域×2字型×4尺寸×2影像條件。兩種字型 cmap 沒有 missing glyph，contact sheets 逐欄核對。由 assistant 進行視覺核對，尚未有人類獨立複核。

這些包括真實官方排版與收據範例，沒有客戶實際工作文件。公開表單大部分是空白表的標籤，填寫金額／公司名的覆蓋不足；不得稱為台灣會計產業整體準確率。4K 只有一行 10px 繁中／金額文字，並非密集桌面畫面。每個候選固定相同順序，因此仍可能受共享 host 負載／熱狀態影響。詳見 BENCHMARK_PROTOCOL.md。

## Recognition 與 Detection 分開

| 候選 | 資料組 | GT字元 N | S (S/N) | D (miss D/N) | I (I/N) | CER | 空白 edits/N | 整 crop exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v4 基線 | public_form | 2229 | 74 (3.32%) | 53 (2.38%) | 4 (0.18%) | 5.88% | 0.27% | 57.66% |
| v4 基線 | synthetic | 2032 | 250 (12.30%) | 183 (9.01%) | 1 (0.05%) | 21.36% | 3.00% | 14.58% |
| v5 mobile | public_form | 2229 | 70 (3.14%) | 6 (0.27%) | 1 (0.04%) | 3.45% | 0.31% | 73.42% |
| v5 mobile | synthetic | 2032 | 166 (8.17%) | 68 (3.35%) | 1 (0.05%) | 11.56% | 2.17% | 27.08% |
| v6 small | public_form | 2229 | 27 (1.21%) | 4 (0.18%) | 6 (0.27%) | 1.66% | 0.45% | 83.78% |
| v6 small | synthetic | 2032 | 129 (6.35%) | 48 (2.36%) | 0 (0.00%) | 8.71% | 2.36% | 27.08% |
| v4 det＋v6 rec／modern | public_form | 2229 | 27 (1.21%) | 4 (0.18%) | 6 (0.27%) | 1.66% | 0.45% | 83.78% |
| v4 det＋v6 rec／modern | synthetic | 2032 | 129 (6.35%) | 48 (2.36%) | 0 (0.00%) | 8.71% | 2.36% | 27.08% |
| v4 det＋v6 rec／legacy | public_form | 2229 | 27 (1.21%) | 4 (0.18%) | 6 (0.27%) | 1.66% | 0.45% | 83.78% |
| v4 det＋v6 rec／legacy | synthetic | 2032 | 129 (6.35%) | 48 (2.36%) | 0 (0.00%) | 8.71% | 2.36% | 27.08% |

NFC 保留 literal 標點、空白和「台／臺」。S=替換、D=刪除、I=插入；miss rate=D/N，substitution=S/N，insertion=I/N。Recognition D 不能當 detector miss。raw 未轉換輸出與 raw_metrics 也已保存。候選間同 crop SHA256 完全相同；v6、modern hybrid、legacy hybrid 的 raw recognition 輸出全數一致。

| 候選 | 資料組 | CER 差異（百分點） | cluster bootstrap 95% CI（百分點） | clusters |
| --- | --- | --- | --- | --- |
| v5 mobile | public_form | -2.42 | [-3.85, -0.94] | 74 |
| v5 mobile | synthetic | -9.79 | [-17.88, -4.89] | 12 |
| v6 small | public_form | -4.22 | [-5.49, -3.06] | 74 |
| v6 small | synthetic | -12.65 | [-22.97, -6.41] | 12 |
| v4 det＋v6 rec／modern | public_form | -4.22 | [-5.49, -3.06] | 74 |
| v4 det＋v6 rec／modern | synthetic | -12.65 | [-22.97, -6.41] | 12 |
| v4 det＋v6 rec／legacy | public_form | -4.22 | [-5.49, -3.06] | 74 |
| v4 det＋v6 rec／legacy | synthetic | -12.65 | [-22.97, -6.41] | 12 |

CI 為固定 seed、2,000 次 cluster bootstrap：公開按原始欄位74clusters，合成按font/domain12clusters；只反映有限選定資料的重抽樣，不能推論外部客戶文件。追加 legacy 候選為探索性，沒有獨立 holdout。

| 候選 | 資料組 | targets | IoU50 recall | coverage80 | selected-field CER | 零覆蓋 targets | 失敗圖 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v4 基線 | public_page | 222 | 98.65% | 96.40% | 7.81% | 0 | 0 |
| v4 基線 | synthetic_page | 24 | 54.17% | 66.67% | 51.28% | 4 | 0 |
| v4 基線 | shape | 5 | 80.00% | 80.00% | 41.00% | 1 | 0 |
| v5 mobile | public_page | 222 | 78.83% | 98.65% | 11.17% | 0 | 0 |
| v5 mobile | synthetic_page | 24 | 100.00% | 100.00% | 11.06% | 0 | 0 |
| v5 mobile | shape | 5 | 40.00% | 40.00% | 68.00% | 3 | 1 |
| v6 small | public_page | 222 | 98.20% | 98.65% | 6.77% | 0 | 0 |
| v6 small | synthetic_page | 24 | 45.83% | 66.67% | 47.92% | 8 | 0 |
| v6 small | shape | 5 | 40.00% | 60.00% | 52.00% | 2 | 0 |
| v4 det＋v6 rec／modern | public_page | 222 | 98.65% | 96.40% | 2.87% | 0 | 0 |
| v4 det＋v6 rec／modern | synthetic_page | 24 | 54.17% | 66.67% | 46.47% | 4 | 0 |
| v4 det＋v6 rec／modern | shape | 5 | 80.00% | 80.00% | 37.00% | 1 | 0 |
| v4 det＋v6 rec／legacy | public_page | 222 | 98.65% | 96.40% | 2.87% | 0 | 0 |
| v4 det＋v6 rec／legacy | synthetic_page | 24 | 54.17% | 66.67% | 46.47% | 4 | 0 |
| v4 det＋v6 rec／legacy | shape | 5 | 80.00% | 80.00% | 37.00% | 1 | 0 |

Detection 的 IoU／coverage 都使用 detector-only output，已映射回原圖；Hungarian matching 為一對一，避免一個大框算多個正確欄位。Coverage 用 polygon 聯集補充診斷，不能替代定位品質。零覆蓋target表示沒有任何detector polygon與該GT框相交，與有框但IoU不達標分開列示。24張小字圖中完全無框的圖數：v4為4、v6為8；同時兩者coverage80都是16/24，因此不可將IoU差異全歸為完全漏字。Selected-field CER 使用 final boxes 的一對一匹配，IoU<0.1 視為空結果；受框切分影響，**不是全頁 transcript CER**。完整單行 synthetic transcript CER 留在 phase2-summary.json。公開未標全頁，因此 precision／全頁閱讀順序 UNKNOWN。

## 台灣繁中、罕字與小字

| 領域 | v4 CER | v5 CER | v6 CER | legacy hybrid CER |
| --- | --- | --- | --- | --- |
| accounting | 20.25% | 12.50% | 9.00% | 9.00% |
| complex | 67.71% | 25.52% | 14.58% | 14.58% |
| context | 24.58% | 12.08% | 8.33% | 8.33% |
| invoice | 4.12% | 2.06% | 0.00% | 0.00% |
| mixed | 0.47% | 0.47% | 0.47% | 0.47% |
| nhi | 5.33% | 4.24% | 2.55% | 2.55% |
| rare | 74.31% | 56.25% | 47.92% | 47.92% |
| receipt | 5.13% | 4.27% | 2.99% | 2.99% |
| tax | 12.98% | 5.53% | 5.05% | 5.05% |
| tax401 | 7.17% | 2.96% | 1.02% | 1.02% |

| 原始 font px | v4 CER | v5 CER | v6 CER | legacy hybrid CER |
| --- | --- | --- | --- | --- |
| 8 | 27.17% | 21.65% | 14.37% | 14.37% |
| 10 | 22.83% | 12.80% | 8.27% | 8.27% |
| 14 | 17.72% | 6.69% | 6.69% | 6.69% |
| 24 | 17.72% | 5.12% | 5.51% | 5.51% |

這裡的 pixel size 是原始合成圖字型大小；recognition crop 進入各 recognizer 的內部 resize，pipeline 則先走現行 short-side resize。兩條路徑不同，不能把「固定 crop 可辨識」當成 capture 全流程能找到該字。

| 候選 | decoder symbols（含特殊項） | GT unique CJK | 直接缺字 |
| --- | --- | --- | --- |
| v4 基線 | 6625 | 221 | 僅、別、則、尞、屬、帳、幣、彙、憑、欄、燊、禛、稅、稱、積、簽、編、繳、註、議、責、貸、賦、鑑、險、項、額、鬱、齒、龜、𠀋、𠮷 |
| v5 mobile | 18385 | 221 | 尞、𠀋、𠮷 |
| v6 small | 18710 | 221 | 尞、𠀋、𠮷 |
| v4 det＋v6 rec／modern | 18710 | 221 | 尞、𠀋、𠮷 |
| v4 det＋v6 rec／legacy | 18710 | 221 | 尞、𠀋、𠮷 |

| Stage | 實際 session model file | SHA256 | Provider |
| --- | --- | --- | --- |
| det | ch_PP-OCRv4_det_infer.onnx | d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9 | CPUExecutionProvider |
| rec | ch_PP-OCRv4_rec_infer.onnx | 48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b | CPUExecutionProvider |
| cls | ch_ppocr_mobile_v2.0_cls_infer.onnx | e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c | CPUExecutionProvider |
| det | ch_PP-OCRv5_det_mobile.onnx | 4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae | CPUExecutionProvider |
| rec | ch_PP-OCRv5_rec_mobile.onnx | 5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5 | CPUExecutionProvider |
| det | PP-OCRv6_det_small.onnx | 090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f | CPUExecutionProvider |
| rec | PP-OCRv6_rec_small.onnx | 6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884 | CPUExecutionProvider |

以上字表從實際已載入 session 的 decoder 取出；完整列表、model path／hash／CPU provider 留在各 identity.json。字表包含字符不代表能正確辨識；單字 zh-hant 可達性另存 vocabulary-coverage.json，不冒充台灣專業詞庫。Repository custom_tw_corrections 的 orphan 結論未改變，本輪沒有偷偷套 replace table。

## Latency／RSS／CPU

環境：Linux x86_64、Python3.12.14、Xeon Platinum8573C、可見9CPU／配額8CPU；ORT1.30.0、legacy1.4.4、modern3.9.2、OpenCV4.10.0。ORT inter/intra各2，OpenCV2，OMP/OpenBLAS2；有效 config、affinity、版本與 trace 留存。不同於第一階段環境，禁止跨階段速度比較。

| 候選 | construction ms | first OCR ms | process peak MiB | ru_maxrss MiB | end RSS MiB | CPU sec | end native threads | det＋rec＋cls MiB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v4 基線 | 250.2 | 9483.6 | 1040.4 | 1039.6 | 296.4 | 491.8 | 7 | 15.44 |
| v5 mobile | 529.9 | 5298.2 | 809.6 | 826.6 | 405.1 | 240.9 | 7 | 21.02 |
| v6 small | 513.3 | 6029.0 | 1155.6 | 1154.9 | 364.0 | 767.9 | 7 | 30.28 |
| v4 det＋v6 rec／modern | 549.1 | 6397.0 | 1299.4 | 1298.9 | 473.7 | 441.0 | 7 | 25.33 |
| v4 det＋v6 rec／legacy | 242.5 | 8436.1 | 1060.9 | 1060.2 | 309.2 | 449.0 | 7 | 25.33 |

construction 包含 candidate import 與 session 建構，不是 Qt 整個應用 cold startup。first OCR 是完整401表96DPI第一次；產品本身的 warm-up dummy 不在此 harness。process peak 包含41圖重複推論、detector probes、318recognition、取模型hash和分析物件；不能當單次 OCR 峰值。10ms RSS sampling 與 ru_maxrss 可能有短峰差異；未用「GC最後會回收」判定沒有 leak。end RSS 是一次負載末端，不是經長時閒置驗證的 steady-state。native threads 為程序末端觀測值，不代表無短暫 thread。

| 候選 | 資料組 | 成功 calls | p50 ms | p95 ms | p99 ms | 第2次 call p50 ms |
| --- | --- | --- | --- | --- | --- | --- |
| v4 基線 | public_page | 24 | 1962.3 | 10035.5 | 13739.9 | 1905.3 |
| v4 基線 | synthetic_page | 48 | 865.2 | 2205.3 | 2475.8 | 852.8 |
| v4 基線 | shape | 10 | 666.8 | 1094.7 | 1120.4 | 635.0 |
| v5 mobile | public_page | 24 | 1658.2 | 5945.0 | 6044.2 | 1586.5 |
| v5 mobile | synthetic_page | 48 | 126.7 | 159.6 | 162.3 | 125.2 |
| v5 mobile | shape | 8 | 172.6 | 333.8 | 336.1 | 162.7 |
| v6 small | public_page | 24 | 2558.1 | 9954.7 | 11323.1 | 2589.5 |
| v6 small | synthetic_page | 48 | 2452.7 | 3971.5 | 4636.3 | 2378.3 |
| v6 small | shape | 10 | 2037.5 | 2288.0 | 2294.2 | 1957.6 |
| v4 det＋v6 rec／modern | public_page | 24 | 1822.0 | 6472.9 | 6842.6 | 1822.0 |
| v4 det＋v6 rec／modern | synthetic_page | 48 | 1021.1 | 2488.7 | 3511.1 | 996.0 |
| v4 det＋v6 rec／modern | shape | 10 | 682.8 | 1053.1 | 1164.4 | 672.5 |
| v4 det＋v6 rec／legacy | public_page | 24 | 1844.8 | 8204.7 | 8415.4 | 1801.6 |
| v4 det＋v6 rec／legacy | synthetic_page | 48 | 974.6 | 1977.0 | 2753.7 | 904.3 |
| v4 det＋v6 rec／legacy | shape | 10 | 697.9 | 1073.8 | 1098.0 | 683.8 |

每圖只兩次，p95／p99是這批異質工作負載的經驗分位數；不是穩定尾延遲 gate。錯誤圖的快速失敗沒有混進成功 latency，但已計入失敗率及缺字。只比平均毫秒會掩蓋 v5 的失敗。

## Resize 實驗

| 候選 | app peak MiB | bounded peak MiB | app CPU sec | bounded CPU sec | app CER | bounded CER | 失敗 app/bounded |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v4 基線 | 951.5 | 663.2 | 40.1 | 33.0 | 41.00% | 39.00% | 0/0 |
| v5 mobile | 688.4 | 432.1 | 10.7 | 8.4 | 68.00% | 67.00% | 1/1 |
| v6 small | 1118.6 | 744.4 | 61.0 | 72.3 | 52.00% | 52.00% | 0/0 |
| v4 det＋v6 rec／modern | 1309.7 | 743.1 | 35.4 | 33.1 | 37.00% | 35.00% | 0/0 |

此表每個 app/bounded 配對都在 fresh process 跑相同五張圖、相同順序；不是拿已跑過表單的 resident heap 與乾淨程序比。bounded：scale≤3、long-side≤2560、pixels≤4M，未導入產品。30×2000 在 app 產生960×64000×3 uint8＝175.78MiB中間 ndarray；bounded 為38×2560，約0.28MiB。process RSS 不等於這單一 ndarray 大小。

CONFIRMED：這組 cap 降低本負載的 process peak，但不是品質無損修正；v4 的30×2000 CER 由20%升至30%。v6的CPU秒反而增加，不能宣稱普遍加速。v5在兩種resize policy都於30×2000失敗：其 max-side960後短邊向32對齊變0。保持aspect ratio的外部cap沒有消除此根因。

4K的10px文字在v4及兩種v4-det組合均未偵得可保留文字；v6能產生部分框但多個字錯。這只能證實該固定case失敗，未分離「app downscale」與「detector內部resize」的獨立因果，需要原尺寸／tile對照才能決定修法。本輪不直接選 tiling／scale cap。

## 留下的 UNKNOWN

候選100／500次長期RSS趨勢、全產品cold startup、多視窗、clipboard高頻、SQLite／WAL增長、Windows DPI／RDP／hotkey、secondary first-load與shutdown stress並未在第二階段重跑。第一階段相應證據仍有效於其固定配置，但不能外推到新模型／Runtime。發布需在目標Windows／真實資料補測。

## Evidence 索引

- `evidence/*-app/identity.json`：session實際path／hash／字表；`effective-config.yaml`：modern有效配置。
- `pipeline.json`：每圖尺寸、latency、框／文字／confidence、錯誤與detection／retrieval配對。
- `recognition.json`：每cropGT／SHA256／raw與converted輸出／edit分解。
- `profiler.json`：10ms RSS／CPU／native threads時間序列。
- `phase2-summary.json`、`regressions.json`、`improvements.json`：可由analyze.py重建。
- `v5-app-aborted`、`harness-history.json`：保留原始失敗與exception ledger修訂；無刪除失敗樣本。
