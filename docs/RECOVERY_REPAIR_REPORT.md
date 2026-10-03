# 1.6.2-rc.2 復原修復候選

日期：2026-10-03。延續同一 Draft PR #1／`fix/offline-release-hardening`，不合併、不發佈正式 Release。

## 復原範圍

已取回 GitHub 上確實保存的 `bba210ec71c7d08d0093c11dba3964d80d0dfb27`，包含三個 production v4 模型、原始審計文件、修復程式與測試。本輪先重跑基準，156 passed、0 failed；環境 exact requirements-dev 與 pip check 通過。

先前對話曾回報尚未推送的192項測試／v4-v6切片工作，但沒有取得其程式、patch或模型位元組。以下修復從已推送基準重新實作，不能稱為完整恢復那份未保存工作。三個 production ONNX 模型不變；v6候選的12個既有回退、罕字字表與部署gate仍未通過。

## 本轮實質修正

1. OCR first-pass成果在enhancement／second-pass失敗時保留；弱框不再被整頁平均掩蓋，raw候選／繁簡轉換／座標與錯誤可追溯
2. 長條／大圖逐片有界辨識，重疊區去重與保留新增coverage；任何切片輸出均待覆核，沒有把confidence當完整性保證
3. durable OCR attempts、latest job／edit revision；manual edits與last-good文字保留，遲到、已刪除、obsolete與重复結果不自動發佈
4. 編輯器重跑、unsaved draft／note與revision衝突保護；清理journal在unlink後metadata失敗時可重啟對帳，訊息不假稱原圖仍存在
5. exact RGBA hash包含alpha及單像素差；dedup的同秒latest次序与未來時間窗口受測。capture檔以exclusive create＋fsync避免碰撞覆寫，invalid monitor／region不默默轉去其他螢幕
6. smoke SQLite verification使用明確close；success與error皆關閉，GC停用的真實Linuxapp退出後DB／WAL／SHM descriptor皆為0，避免Windows temporary-home清理受open handle阻擋。這仍待native Windows確認
7. portable single-instance lifetime與startup exception清理；source／frozen probe schema2、EXE SHA256、版本與SQLite／thread lifecycle檢核，建置成功不等於clean-machine驗收

Schema v5為additive migration；既有items、人工文字、FTS均有保留測試。正式使用前仍應備份整個data/config目錄；退回rc.1不會知道新attempt/journal，不能把舊程式當新格式的恢復工具。

## 實際OCR結果與界線

七張固定合成圖使用同一v4 session配對：兩張原本整次failed的長條圖，edit errors由32→1、20→3；一張4K／10px圖17→4。一般mixed-text維持exact；繁中一般樣本3→3、罕字12→12，2300×600樣本4→4且錯誤分布改變。詳見[像素／GT／原始輸出](OCR_RECOVERY_20261003.md)。

這不是現場整體CER改善。普通小圖仍保留vendor resize相容路徑；切片的單次4K觀察約1.8秒、同process sampled RSS最高約985MiB，沒有長期leak／低峰值保證。

## 驗證證據

- baseline156 tests：`evidence/recovery/baseline-tests.xml`
- 最終完整suite：297 passed、0 failed、0 skipped，9.35s；`evidence/recovery/final-tests.xml`
- source self-test／full app smoke：`evidence/recovery/source-selftest.json`、`source-app-smoke.json`
- 上述兩個Linux source probes在per-process libseccomp網路syscall EPERM guard下執行，guard實際驗證IPv4／IPv6／Unix socket拒絕；對應`*-guard.json`。strace因環境ptrace權限限制未執行，沒有socket trace／wire payload觀察。seccomp不關閉既有descriptor，也不模擬乾淨Windows機器
- smoke驗證connection lifetime：`evidence/recovery/sqlite-lifetime.json`，不靠gc釋放，Windows尚待native CI
- dependency環境：`evidence/recovery/pip-freeze.txt`
- synthetic像素、GT、source/model/font hashes與raw結果：`evidence/ocr-recovery-20261003/`

最終commit與CI/artifact狀態以PR記錄核對。舊baseline／model benchmark文保留為歷史證據，不改寫成這次的實測結果。

## 仍阻擋正式交付

- 無Python、無模型快取、真正斷網的乾淨Windows機器與同一ZIP冷／第二次啟動未驗收
- mixed DPI／多螢幕框選仍有primary-screen assumption；invalid-index保護不代表已支援所有配置
- 罕字、rotation／直排／table、真實現場holdout與金額／日期／姓名exact gates未過
- native OCR永不返回時，QThread不能安全強制終止；保留安全drain與stall提示，真正有界退出仍需process isolation
- retention、完整dependency hash lock／license／signing、未接線設定及歷史Microsoft payload仍開放

GitHub runner的Windows frozen／firewall probe只支持該runner及EXE路徑；不能代替乾淨離線使用者機器驗收。
