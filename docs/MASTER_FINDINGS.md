# Master 修復追蹤表

本表對照固定基準 master 的49項發現。完整位置、Class/Function、Call Path、根因、重現、四類影響、修法、測試與 regression dossier 保留於 [原始 MASTER_FINDINGS](audit-baseline/MASTER_FINDINGS.md)。Severity／證據標記沿用原始發現，並不表示修復後仍具相同 impact。

2026-10-03 追加恢復修復：見 [復原報告](RECOVERY_REPAIR_REPORT.md) 與 [七張原始像素比較](OCR_RECOVERY_20261003.md)。最新完整測試以 `docs/evidence/recovery/final-tests.xml` 為準；舊156項是 bba210ec 的歷史結果。

**已驗證修復只適用表內測試範圍；不得解讀為所有平台及現場 gate 通過。**

| ID | Severity | 原始證據 | 本次狀態 | 修正／剩餘驗證 |
|---|---|---|---|---|
| [O01](audit-baseline/MASTER_FINDINGS.md#o01) | High | CONFIRMED | 部分修復 | 語言／間距 policy 與 GT；24 圖重播 CER 不變，保留場景 regression gate |
| [O02](audit-baseline/MASTER_FINDINGS.md#o02) | High | CONFIRMED | 部分修復 | 跨 pass coverage 仲裁、失敗保留、raw hypotheses 與原座標回映；切片 seam 負例受測，旋轉／表格未驗收 |
| [O03](audit-baseline/MASTER_FINDINGS.md#o03) | High | CONFIRMED | 部分修復 | 單框低信心觸發重試／review；沒有框的 detection 漏字仍未知，未以新模型掩蓋 |
| [O04](audit-baseline/MASTER_FINDINGS.md#o04) | High | CONFIRMED | 開放 | Phase 2 v6 候選有改善與12個 exact-match 回退；仍未換入產品 |
| [O05](audit-baseline/MASTER_FINDINGS.md#o05) | Medium | CONFIRMED | 開放 | custom_tw_corrections 仍 orphan，文件移除 runtime 詞庫宣稱 |
| [O06](audit-baseline/MASTER_FINDINGS.md#o06) | Medium | CONFIRMED | 部分修復 | manifest 驗證並傳入實際 model paths；舊 config model_* 不再作可切換承諾 |
| [O07](audit-baseline/MASTER_FINDINGS.md#o07) | High | CONFIRMED | 已驗證修復 | primary inference exception 路由 fallback 的 contract 測試；Core 不啟用 secondary |
| [O08](audit-baseline/MASTER_FINDINGS.md#o08) | High | CONFIRMED | 部分修復 | 3.x Mapping／rec_polys／numpy BGR 與錯誤格式拒絕受測；native optional 未驗收 |
| [O09](audit-baseline/MASTER_FINDINGS.md#o09) | Medium | CONFIRMED | 部分修復 | device 傳入與 local-model-dir guard；optional 模型世代／hash manifest 仍待整合 |
| [O10](audit-baseline/MASTER_FINDINGS.md#o10) | Medium | INFERRED | 開放 | 跨引擎 confidence 未校準，Core 不啟用此路徑 |
| [P01](audit-baseline/MASTER_FINDINGS.md#p01) | High | CONFIRMED | 部分修復 | 解碼前32MB／32M來源像素及逐片工作預算；長條／4K合成配對已執行，現場accuracy／隔離RSS仍未過 |
| [P02](audit-baseline/MASTER_FINDINGS.md#p02) | Medium | CONFIRMED | 已驗證修復 | fallback 後總耗時在最終結果時計算 |
| [P03](audit-baseline/MASTER_FINDINGS.md#p03) | Medium | CONFIRMED | 已驗證修復 | 500次settings close後SettingsDialog child count=0；不是整體RSS改善證明 |
| [P04](audit-baseline/MASTER_FINDINGS.md#p04) | Medium | CONFIRMED | 已驗證修復 | 100次editor close後dict為空且EditorWindow child count=0 |
| [C01](audit-baseline/MASTER_FINDINGS.md#c01) | High | CONFIRMED | 已驗證修復 | 真實 QThread idle resubmit／drain；持續 worker 單一 lifetime |
| [C02](audit-baseline/MASTER_FINDINGS.md#c02) | High | CONFIRMED | 部分修復 | 40 工作 capture→DB→OCR→DB 退出 stress 通過；Windows forced shutdown 尚未驗收 |
| [C03](audit-baseline/MASTER_FINDINGS.md#c03) | Medium | CONFIRMED | 部分修復 | thread-local connections／交易鎖與 rollback；UI 部分寫入仍同步，文件已改正 |
| [C04](audit-baseline/MASTER_FINDINGS.md#c04) | Medium | INFERRED | 部分修復 | Pipeline QObject slots＋overlay context／callback snapshot；Windows pending callback gate 仍開放 |
| [D01](audit-baseline/MASTER_FINDINGS.md#d01) | High | CONFIRMED | 已驗證修復 | DTO／durable attempts 保存實際 engine/model_version 與完整raw/tile provenance；未知來源明記unknown |
| [D02](audit-baseline/MASTER_FINDINGS.md#d02) | High | CONFIRMED | 已驗證修復 | commit acknowledgement＋清理journal；unlink後metadata故障可在重啟對帳，錯誤訊息反映實際檔案狀態 |
| [D03](audit-baseline/MASTER_FINDINGS.md#d03) | Medium | CONFIRMED | 已驗證修復 | clear_image_paths 同時重算 item_type；empty/failed 保留圖片 |
| [D04](audit-baseline/MASTER_FINDINGS.md#d04) | Medium | CONFIRMED | 已驗證修復 | manual edits保留，空編輯不回退；failed/empty重跑保存last-good，job/revision拒絕obsolete／deleted發佈 |
| [D05](audit-baseline/MASTER_FINDINGS.md#d05) | Medium | CONFIRMED | 已驗證修復 | 匯出 microsecond＋UUID 避免同秒覆寫 |
| [S01](audit-baseline/MASTER_FINDINGS.md#s01) | High | CONFIRMED | 部分修復 | 新 profile monitor=false，UI 提示明文／無 retention；舊 profile保留，retention仍未實作 |
| [S02](audit-baseline/MASTER_FINDINGS.md#s02) | Medium | CONFIRMED | 部分修復 | import 前 ORT opt-out／frozen runtime hook；歷史 payload UNKNOWN，Windows目的地歸因仍待驗收 |
| [S03](audit-baseline/MASTER_FINDINGS.md#s03) | Medium | CONFIRMED | 已驗證修復 | CSV 危險字首文字化，JSON/TXT保留原值，負號影響已註明 |
| [S04](audit-baseline/MASTER_FINDINGS.md#s04) | Medium | CONFIRMED | 部分修復 | FileManager／ZIP／刪除相對路徑 containment 及 symlink rejection；同使用者TOCTOU非權限隔離 |
| [S05](audit-baseline/MASTER_FINDINGS.md#s05) | Low | CONFIRMED | 部分修復 | ItemCard PlainText／Enter／Space native Qt tests通過；完整accessibility仍待驗收 |
| [F01](audit-baseline/MASTER_FINDINGS.md#f01) | High | CONFIRMED | 已驗證修復 | 真 TagRepository 設定對話框建構／統計測試，補齊 Qt imports |
| [F02](audit-baseline/MASTER_FINDINGS.md#f02) | Medium | CONFIRMED | 已驗證修復 | 移除沒有 Runtime consumer 的 primary/autoswitch 控制項，不冒充支援 |
| [F03](audit-baseline/MASTER_FINDINGS.md#f03) | High | CONFIRMED | 已驗證修復 | 移除雙套secondary UI，Core固定引擎；參數明示重啟套用，沒有GUI同時configure |
| [F04](audit-baseline/MASTER_FINDINGS.md#f04) | Medium | CONFIRMED | 已驗證修復 | 不再提供不存在 factory name 的 app 選項；factory canonical names contract受測 |
| [F05](audit-baseline/MASTER_FINDINGS.md#f05) | Medium | CONFIRMED | 部分修復 | Tag update／usage count 受測，選取項目套用已接線待native互動驗收 |
| [F06](audit-baseline/MASTER_FINDINGS.md#f06) | Medium | CONFIRMED | 已實作待實測 | MainWindow export-all 改為 _item_repo；實際大筆數匯出UI gate仍待驗收 |
| [F07](audit-baseline/MASTER_FINDINGS.md#f07) | Medium | CONFIRMED | 開放 | 停用部分未實作控制項，auto_capture/start_min/single_instance/opacity/quick_search 接線；其餘見舊矩陣 |
| [W01](audit-baseline/MASTER_FINDINGS.md#w01) | Medium | CONFIRMED | 開放 | 仍有 primary screen／MSS monitor1 assumption；mixed DPI不可宣稱解決 |
| [W02](audit-baseline/MASTER_FINDINGS.md#w02) | Medium | CONFIRMED | 已實作待實測 | 取消／太小框／失敗回復浮窗；Windows互動尚待驗收 |
| [W03](audit-baseline/MASTER_FINDINGS.md#w03) | Low | CONFIRMED | 部分修復 | Windows HANDLE與例外清理／POSIX process lock受測；喚醒receiver仍未實作 |
| [U01](audit-baseline/MASTER_FINDINGS.md#u01) | Medium | CONFIRMED | 部分修復 | 次要字色 contrast fixture>=4.5；卡片鍵盤動作，完整focus/accessibility未驗收 |
| [B01](audit-baseline/MASTER_FINDINGS.md#b01) | Medium | CONFIRMED | 已驗證修復 | collect/hidden/exclude集合衝突測試＋Windows候選onefile build |
| [B02](audit-baseline/MASTER_FINDINGS.md#b02) | Medium | CONFIRMED | 已驗證修復 | UI/README取消host pip即可擴充既有EXE的錯誤宣稱，部署方案保留分析 |
| [B03](audit-baseline/MASTER_FINDINGS.md#b03) | Medium | CONFIRMED | 部分修復 | repo模型已進Runtime與build；舊download脚本／供應鏈hash gate待整理 |
| [B04](audit-baseline/MASTER_FINDINGS.md#b04) | Medium | CONFIRMED | 部分修復 | Core版本固定且單一opencv-python；transitive/wheel hash lock及license仍開放 |
| [T01](audit-baseline/MASTER_FINDINGS.md#t01) | High | CONFIRMED | 已驗證修復 | bba210ec基準156 passed；rc.2完整結果见final-tests.xml，舊133/17failed原始證據保留 |
| [T02](audit-baseline/MASTER_FINDINGS.md#t02) | Medium | CONFIRMED | 部分修復 | 新增CI、real Qt/SQLite adversarial與frozen smoke；非clean-machine替代 |
| [X01](audit-baseline/MASTER_FINDINGS.md#x01) | Low | CONFIRMED | 部分修復 | 現行主要README/packaging/reviewer/smoke/OCR reports更新；baseline歷史文不改成現在狀態 |
| [X02](audit-baseline/MASTER_FINDINGS.md#x02) | Low | CONFIRMED | 開放 | validator已接線，legacy preprocessor／signal bus／dictionary等保留為已知orphan |
| [D06](audit-baseline/MASTER_FINDINGS.md#d06) | Medium | CONFIRMED | 部分修復 | dedup拒收時清理capture/thumb；unlink故障仍可能殘留，需reconciliation gate |
| [D07](audit-baseline/MASTER_FINDINGS.md#d07) | Medium | CONFIRMED | 已驗證修復 | 精確RGBA dimensions/pixels SHA256，alpha／單像素差負例；同秒latest tie與future-time window受測 |

## 下一輪順序

1. 目前同一 commit 的 Windows full startup/shutdown、frozen self-test 與 dry build。失敗即修正並重新產生證據。
2. Windows primary-screen／mixed-DPI、全域熱鍵／喚醒、OS session ending；取得 clean-machine artifact acceptance。
3. 台灣現場 GT 的 detection/recognition 分離、rotation/table fusion；長條切片已有七圖合成CER／RSS配對，現場holdout與isolated峰值尚待驗收。
4. v4 det＋v6 rec 候選的12個回退及罕字 gate；通過前保持 v4。
5. 明確 retention／檔案 reconciliation、剩餘設定與孤兒路徑整理；不得默默啟用舊有破壞性保留值。
6. 固定 Windows wheels／licenses／簽章與同一 artifact Release gate，完成後才更新正式 Release。

## 自我檢查

- 沒有因 README／config／tests 存在而宣稱 Runtime 功能。
- provider mock修成正確contract，明列native integration UNKNOWN。
- 500次profiler與24圖spacing replay保存原始數值；後者CER未改善，明確寫出。
- 缺少權限／平台／GT的部分保留UNKNOWN與開放狀態，沒有把Windows CI當乾淨電腦。
- 保留baseline audit與當前修復狀態的版本邊界，不用改寫舊報告隱藏問題。
