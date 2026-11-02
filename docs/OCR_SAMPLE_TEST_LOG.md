# OCR 測試紀錄

基準 HEAD 的測試與 profiler 保留於 [審計](audit-baseline/MASTER_FINDINGS.md)。模型比較使用固定 GT／模型 hashes，見 [協定](model-benchmark/BENCHMARK_PROTOCOL.md)與 [結果](model-benchmark/PHASE2_RESULTS.md)。

歷史修復候選證據；含執行機資料的原始紀錄已移出目前樹，見 [去識別歷史摘要](evidence/HISTORICAL_VALIDATION_20261003.md)：

- [歷史pytest摘要與原始hash](evidence/HISTORICAL_VALIDATION_20261003.json)：各階段結果分列，並非目前候選的完整測試通過證據。
- [歷史source self-test摘要](evidence/HISTORICAL_VALIDATION_20261003.md)：Linux／Qt offscreen、frozen=false；含模型絕對路徑的原JSON不再提供於目前樹。
- [500 次 OCR profiler](evidence/runtime-500.json)：全部 latency 與20ms RSS samples，限制見 [PERFORMANCE_VALIDATION](PERFORMANCE_VALIDATION.md)。
- [空格重播](evidence/spacing-replay.json)：24 張圖、624 GT 字元，前後 CER 相同；沒有宣稱準確度改善。

Windows CI／frozen 成品與乾淨 VM 的狀態見 [VALIDATION_STATUS](VALIDATION_STATUS.md)。未執行的 gate 明列 UNKNOWN。
