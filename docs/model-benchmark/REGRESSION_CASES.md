# 回歸與遷移阻擋案例

審計基準：`maotai11/desktop-ocr-tool@f5af545cabc4e9a649cd57497fbf5bf42f2ff138`。日期：2026-10-02。產品原始碼、設定與正式模型未變更。CONFIRMED＝本批程式／實測證據；INFERRED＝候選解釋；UNKNOWN＝仍未驗證。

## 配對回歸數量

| 候選 | error增加 crops | error減少 crops | v4 exact→不exact | 其中NFKC等價 | 其中僅空白差 |
| --- | --- | --- | --- | --- | --- |
| v5 mobile | 24 | 132 | 17 | 9 | 3 |
| v6 small | 12 | 155 | 12 | 5 | 1 |
| v4 det＋v6 rec／modern | 12 | 155 | 12 | 5 | 1 |
| v4 det＋v6 rec／legacy | 12 | 155 | 12 | 5 | 1 |

計數以同一GT crop的edit數增加／減少為準，相同error數但錯誤位置改變不算改善；兩種字表／字寬形式不任意當成相同。NFKC等價與空白類別可能重疊，不可相加當獨立分類。改善與回歸全文在JSON，不只選漂亮案例。

## 優先整合候選的全部 error 增加案例

| crop ID | GT | v4 | legacy hybrid | errors v4→candidate | confidence |
| --- | --- | --- | --- | --- | --- |
| tax401-144dpi-field150 | 本期(月)應實繳稅額(1－10) | 本期(月)應實繳稅額(1－10) | 本期(月)應實繳稅額(1—10) | 0→1 | 0.9728 |
| tax401-144dpi-field152 | 本期(月)申報留抵稅額(10－1) | 本期(月)申報留抵稅額(10－1) | 本期(月)申報留抵稅額(10—1) | 0→1 | 0.9634 |
| tax401-192dpi-field150 | 本期(月)應實繳稅額(1－10) | 本期(月)應實繳稅額(1－10) | 本期(月)應實繳稅額(1—10) | 0→1 | 0.9798 |
| tax401-192dpi-field152 | 本期(月)申報留抵稅額(10－1) | 本期(月)申報留抵稅額(10－1) | 本期(月)申報留抵稅額(10—1) | 0→1 | 0.9740 |
| nhi-144dpi-field30 | 聯絡電話: | 聯絡電話: | 聯絡電話： | 0→1 | 0.9963 |
| nhi-192dpi-field0 | 聯絡電子郵件信箱帳號: | 聯絡電子郵件信箱帳號: | 聯絡電子郵件信箱帳號： | 0→1 | 0.9950 |
| nhi-192dpi-field30 | 聯絡電話: | 聯絡電話: | 聯絡電話： | 0→1 | 0.9951 |
| nhi-192dpi-field31 | 負責人: | 負責人: | 負責人： | 0→1 | 0.9868 |
| nhi-192dpi-field45 | 投保單位代號: | 投保單位代號: | 投保單位代號： | 0→1 | 0.9978 |
| receipt-192dpi-field4 | 2019-01-23 11:22:33 | 2019-01-23 11:22:33 | 2019-01-2311:22:33 | 0→1 | 0.9814 |
| syn-Sans-mixed-14-clean | API_key AB-12345678 10 kg (1,234) -5.00% | API_key AB-12345678 10 kg (1,234) -5.00% | APl_key AB-12345678 10 kg (1,234) -5.00% | 0→1 | 0.9802 |
| syn-Sans-mixed-14-lowcontrast_blur | API_key AB-12345678 10 kg (1,234) -5.00% | API_key AB-12345678 10 kg (1,234) -5.00% | APl_key AB-12345678 10 kg (1,234) -5.00% | 0→1 | 0.9787 |

以上是 fixed-crop recognition；同pipeline框的文字可能另受detection裁切、分類、spacing影響。各crop在dataset/以ID定位。正確文字的保護gate必須涵蓋這些案例，不能先對GT或結果做全域正規化以消掉回歸。

## Confidence 的實際限制

| 候選 | confidence≥.95 crops | 不exact crops | 解讀 |
| --- | --- | --- | --- |
| v4 基線 | 105 | 35 | 包含空格／字寬／標點；不全部代表語意錯字 |
| v5 mobile | 207 | 48 | 包含空格／字寬／標點；不全部代表語意錯字 |
| v6 small | 261 | 68 | 包含空格／字寬／標點；不全部代表語意錯字 |
| v4 det＋v6 rec／modern | 261 | 68 | 包含空格／字寬／標點；不全部代表語意錯字 |
| v4 det＋v6 rec／legacy | 261 | 68 | 包含空格／字寬／標點；不全部代表語意錯字 |

此分數是模型輸出平均分，不是經本domain校準的「整段文字正確機率」。例如v6在 `syn-Sans-accounting-10-clean` 仍可高分把 `NT$` 輸成 `NTS`；完整confidence及raw字串見recognition.json。顯示高分不能跳過金額／人名／日期review；跨engine比較也不能直接視同概率。

## P2-01 — v5 candidate 極端比例 ResizeImgError

**Medium · CONFIRMED，僅適用本輪候選配置；不列成已部署 master 的 v5 bug。**

- 位置／File／Class／Function：rapidocr3.9.2 `utils/process_img.py::reduce_max_side`；實驗 `benchmark.py::resize/infer`。
- 真實Call Path：30×2000 GT圖 → app upscale960×64000 → modern Global.max_side_len960 → reduce_max_side → 高度14後按32取整成0 → ResizeImgError。
- 根因：極端長寬比與max-side／stride rounding組合沒有保留正尺寸；不是recognizer認錯。
- 重現：`run_all.py` 的v5-app、v5-bounded；初始aborted log、重跑逐圖ledger均保留。
- 使用者影響：若採此候選會整次OCR失敗；現有primary exception fallback缺口須另外處理。
- 資料完整性：本benchmark明確記failed；產品不可把exception轉成空字串後標done。
- 效能：快速失敗不能計成加速；巨大前置ndarray仍已配置。
- 資安／隱私：本例沒有外傳證據；推論有網路阻斷。
- 修正方向：在vendor尺寸邊界保證正shape，並配對查驗是否讓小字不可辨識；必要時拒絕不支援尺寸並回報原因。單加外部max-pixels不足。
- 修正驗證：四種極端比例、旋轉90度、最小寬高與4K；同GT驗無exception、det recall與CER；失敗status能回到UI/DB。
- Regression：簡單clamp32可能改變aspect ratio或內部scale；不能只assert不拋exception。

## P2-02 — modern Runtime 的 return contract 與產品不同

**Medium · CONFIRMED，遷移阻擋；現行 legacy tuple API 本身未因此失效。**

- 位置／File／Class／Function：`src/ocr/engine.py::OcrEngine._do_ocr_array` line185；rapidocr `utils/output.py::RapidOCROutput`。
- 真實Call Path：實際modern RapidOCR/v6模型 → product `_do_ocr_array` → `result, _ = self._engine(image)` → TypeError；api-contract.json留實際錯誤。
- 根因：modern回傳結構化object，不提供legacy tuple解包；安裝新套件／改import不夠。
- 重現：guarded `api_contract_probe.py`，使用真實model與空白圖；沒有MagicMock替代vendor回傳。
- 使用者影響：直接替換Runtime將使OCR呼叫失敗。
- 資料完整性：若錯誤處理掩蓋異常可產生空結果／錯誤狀態；本probe未寫DB。
- 效能：模型載入已花成本但無可用結果；未測故障重試峰值。
- 資安／隱私：API差異本身不證明外傳；modern import另見網路follow-up。
- 修正方向：只在明確engine邊界實作version-specific輸出轉換，含None、empty、boxes、scores與座標；或先用已測legacy＋v6rec候選。
- 修正驗證：真實legacy與modern各跑文字圖／空白／異常，核對DTO及DBstatus／provenance；不可只mock tuple。
- Regression：frame座標、None語義、confidence與空字串處理可能改變，須與固定GT配對。

## P2-03 — 整套 v6 的小字偵測沒有跟著 recognition 改善

**Medium · CONFIRMED（本批24圖），現場發生率 UNKNOWN。**

- 位置／File／Class／Function：vendor detector與其normalization／resize；benchmark detector-only stage。
- 真實Call Path：小字合成原圖 → app short-side resize → v6 detector → 無框或IoU不達標；相同GT crop直接送v6recognizer另行成功與否記錄。
- 根因：CONFIRMED為detection階段的框／召回差異；究竟由weights或preprocess個別造成仍UNKNOWN。不能以rec字表大小解釋漏框。
- 重現：24個固定synthetic_page；v4 13/24、v6 11/24，兩個v4-det hybrid維持13/24。
- 使用者影響：採全套候選可能在小字少出文字；不能用辨識CER降低抵銷。
- 資料完整性：整段漏失比單字低confidence更難從平均分發現。
- 效能：v6整套在本負載有額外成本，沒有用召回改善換得。
- 資安／隱私：沒有此case特有的外傳證據。
- 修正方向：先保持v4det作rec升級控制；另開固定detector參數／weights對照，勿把全部調參混成一次成功。
- 修正驗證：同24圖及新增盲測小字／低對比／真實DPI，測IoU與coverage及recognition；目前基線13/24本來就不足。
- Regression：只針對24圖調threshold會過擬合；需未用於調參的holdout與false-positive標註。
