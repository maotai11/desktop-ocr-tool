# OCR 測試紀錄

基準 HEAD 的測試與 profiler 保留於 [審計](audit-baseline/MASTER_FINDINGS.md)。模型比較使用固定 GT／模型 hashes，見 [協定](model-benchmark/BENCHMARK_PROTOCOL.md)與 [結果](model-benchmark/PHASE2_RESULTS.md)。

修復候選原始證據：

- [pytest JUnit](evidence/full.xml)：實際 collected／passed／failed／skipped；不以固定測試數字宣傳。
- [source self-test](evidence/source-selftest.json)：Qt、三個實際模型、known text、SQLite；frozen=false。
- [500 次 OCR profiler](evidence/runtime-500.json)：全部 latency 與20ms RSS samples，限制見 [PERFORMANCE_VALIDATION](PERFORMANCE_VALIDATION.md)。
- [空格重播](evidence/spacing-replay.json)：24 張圖、624 GT 字元，前後 CER 相同；沒有宣稱準確度改善。

Windows CI／frozen 成品與乾淨 VM 的狀態見 [VALIDATION_STATUS](VALIDATION_STATUS.md)。未執行的 gate 明列 UNKNOWN。
