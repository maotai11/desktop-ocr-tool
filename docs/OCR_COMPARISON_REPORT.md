# OCR 比較報告索引

舊文件中的主觀模型優劣與 mock 測試不能作為模型遷移依據。固定 master HEAD 的完整審計見 [OCR_ACCURACY_AUDIT](audit-baseline/OCR_ACCURACY_AUDIT.md)；實際配對模型 benchmark 見 [PHASE2_RESULTS](model-benchmark/PHASE2_RESULTS.md)。

目前候選版本保持 v4 模型 hashes。v6 recognition 候選有平均 CER 改善與確實回退，同時存在罕字缺字及小字 Detection 問題；尚未換入產品。修復後空格／融合證據及其限制見 [OCR_REPAIR_VALIDATION](OCR_REPAIR_VALIDATION.md)。

確認層級：模型檔案存在、套件可 import、mock 返回結果、native 推論、成品離線可用，各屬不同 gate。
