# Frozen baseline audit

以下12份報告原樣保存，只適用 `master@f5af545cabc4e9a649cd57497fbf5bf42f2ff138`。報告中的「產品 source 未修改」指審計當時；不能解讀為目前修復分支未修改。修復進度以 [當前追蹤表](../MASTER_FINDINGS.md)為準。

原始完整證據包：`desktop-ocr-tool-master-forensic-audit.zip`，289個檔案。原始大型 raw evidence／dataset 未重複加入此 git 目錄；`EVIDENCE_MANIFEST.json` 記錄完整本地證據 hashes。報告内 `evidence/` 路徑指原始證據包，不是本目錄已完整提供。後續修復的合成像素、profiler等部分資料仍在 [docs/evidence](../evidence)。其中含執行機路徑／主機名稱的14份歷史原始log、JUnit與JSON已移出目前樹，改提供 [去識別歷史摘要及原始hash](../evidence/HISTORICAL_VALIDATION_20261003.md)；這不是改寫或清除舊Git歷史。

- [CURRENT_ARCHITECTURE](CURRENT_ARCHITECTURE.md)
- [OCR_ACCURACY_AUDIT](OCR_ACCURACY_AUDIT.md)
- [SECURITY_PRIVACY_AUDIT](SECURITY_PRIVACY_AUDIT.md)
- [PERFORMANCE_MEMORY_AUDIT](PERFORMANCE_MEMORY_AUDIT.md)
- [CONCURRENCY_LIFECYCLE_AUDIT](CONCURRENCY_LIFECYCLE_AUDIT.md)
- [CONFIG_WIRING_MATRIX](CONFIG_WIRING_MATRIX.md)
- [ORPHAN_DEAD_CODE_REPORT](ORPHAN_DEAD_CODE_REPORT.md)
- [TEST_INTEGRITY_REPORT](TEST_INTEGRITY_REPORT.md)
- [PACKAGING_AUDIT](PACKAGING_AUDIT.md)
- [DOCUMENTATION_DRIFT_REPORT](DOCUMENTATION_DRIFT_REPORT.md)
- [UX_ACCESSIBILITY_AUDIT](UX_ACCESSIBILITY_AUDIT.md)
- [MASTER_FINDINGS](MASTER_FINDINGS.md)
