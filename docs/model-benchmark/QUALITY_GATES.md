# 第二階段交付檢查與未過門檻

審計 HEAD：`f5af545cabc4e9a649cd57497fbf5bf42f2ff138`。第二階段是候選測量；第一階段49項 finding 沒有因本輪 benchmark 自動關閉。

## 已核對的證據

| 檢查 | 結果 | 證據 |
|---|---|---|
| Product HEAD／working tree | CONFIRMED：固定HEAD、git status乾淨 | environment.json、validation.json |
| GT未按輸出修改 | CONFIRMED：凍結manifest SHA256相同 | dataset-frozen-sha256.txt；431個唯一圖片資產逐一驗hash |
| 5候選固定cropped GT | CONFIRMED：各318筆，ID／文字／crop hash一致 | recognition.json、validation.json |
| Detection與recognition分離 | CONFIRMED：各41個detector-only probe＋318個rec-only crop | pipeline.json、recognition.json |
| 失敗圖仍在統計 | CONFIRMED：v5的30×2000 retained；共1個主配置失敗圖 | errors ledger、aborted run；success latency不納入該圖 |
| 實際模型載入 | CONFIRMED：native session path／SHA256／decoder字表 | 各identity.json；沒有由UI／檔名推論已載入 |
| legacy hybrid模型控制 | CONFIRMED：41/41 detector boxes與v4完全相同，318/318 rec raw與v6相同 | validation.json |
| Profiler資料 | CONFIRMED：RSS／CPU／native threads時間序列及ru_maxrss | profiler.json；不宣稱長期無leak |
| 網路隔離 | CONFIRMED：先設opt-out，syscall fixture EPERM | guard-validation.json、runner／inference log |
| 新 socket來源 | CONFIRMED：urllib3本機IPv6能力probe | network-origin stack；沒有放行未知payload |
| API相容性實測 | CONFIRMED：modern真實object不能由現行tuple解包 | api-contract.json |
| 圖表 | 已逐張視覺檢查 | recognition-comparison.png、detection-comparison.png |

`validation.json` 的 passed 是證據完整性檢查通過，**不是所有OCR預測正確、產品pytest通過或可發布**。第一階段原始133個tests的17個failure尚未修正，本輪沒有重跑它們來製造新的通過數字。Metric fixtures只驗edit計算與一對一框配對；不能替代真實OCR測量。

## 自我檢查

| 使用者要求的檢查 | 本次處理 |
|---|---|
| 是否相信README而未驗證 | 模型identity取自載入session；modern API以實際模型測TypeError；引用文件不取代執行 |
| 是否把config存在誤認feature | 正式config未改；候選以真實constructor參數傳入，另存effective config；模型path接線仍為未修 gate |
| 是否把tests存在當有效 | 提供GT、逐筆輸出、failure ledger與profiler；沒有宣稱1590個recognition全部passed |
| 是否增加abstraction卻未消根因 | 只寫隔離benchmark；提出保留legacy／det的受控候選，不建立新production框架 |
| 是否使用泛化建議 | 推薦由同crop CER、同detector boxes、API實測支撐；cap因單例品質下降不直接採用 |
| 是否針對台灣繁中 | 官方401、發票、補充保險費；複雜字、罕字、己／已／巳上下文與中英會計格式；沒有泛稱台灣業界準確率 |
| 是否量測memory／latency／CER | 有分組CER、S/D/I、空白error、detector IoU／coverage、p50/p95/p99、RSS／CPU原始trace |
| 是否保留不確定性 | 真實客戶corpus、Windows／packaged app、candidate500次、未知歷史payload、model再散布權利明列UNKNOWN |
| 是否遺漏orphan/dead path | 本輪不改變第一階段orphan清單；custom_tw_corrections沒有進入推論，不宣稱詞庫已工作 |
| 是否呈現文件／UI／Runtime／Test矛盾 | Phase1的config／provider／provenance／打包矛盾保持open；Phase2另外證實modernreturncontract不可直接替換 |

本輪新增v6罕字字表仍缺 `尞、𠮷、𠀋`，且有12個baseline exact變差crop；已在決策報告與回歸全文列明。沒有把全形／半形、空白或台／臺默默正規化掉，也沒有事後挑選有利圖片。

## 尚未通過的 Quality Gates

沒有真實客戶holdout與獨立GT複核，不能宣稱現場繁中OCR全面改善。沒有候選100／500次及secondary／UI stress，不能宣稱模型升級後沒有memory leak或race。沒有Windows clean-machine build，不能宣稱部署完成。沒有歷史wire capture，不能填補先前Microsoft payload的UNKNOWN。模型migration需要前述gate及Phase1根因修正各自通過。

可立即採取的下一步是按 MODEL_UPGRADE_DECISION.md 所列依賴順序，準備小範圍、可回退的整合修改與具體驗收；本次交付沒有執行該產品修改。
