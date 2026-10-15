# InkPick 1.8.0-rc.2 驗收界線

實際完成結果以 Release 同一 EXE／ZIP SHA256、validation metadata、BUILD_MANIFEST 及 SOURCE_MANIFEST 為準；不能沿用舊版或其他 commit 的結果。

## 發布前必須通過

- Windows／Linux tests；source OCR／SQLite／app lifecycle／手寫 probes
- 兩組 PP-OCRv6 權重、字表、tensor／provider identity 及逐字圖片比對
- 手寫 model／alphabet／fixture／native source／helper bytes，Windows x64、靜態 CRT 及系統 DLL imports
- 實際 EXE raster payload、PE icon 與 InkPick 名稱
- 真實 helper 的16筆模型原始筆跡、最新請求、Unicode／self MIME、正常停止
- Windows Job 指派／父程序退出後 helper 終止；啟動失敗／逾時／錯誤輸出／快速關閉
- 外層 EXE 與同次抽出的 helper 被 OS 阻斷對外連線時，frozen probes 仍通過
- Small／Medium 各500次同一 frozen EXE 推論；所有來源／成品／報告 hashes 與同一 run 對應
- ZIP 的模型 XML、LGPL、Zinnia BSD COPYING 及修補來源 bytes 核對

## 仍需現場驗收

- 無 Python／pip／手寫語言套件／模型快取，斷網且一般使用者的 Windows10／11 VM
- 冷啟動／二次啟動與記憶體、繁中含空白路徑、罕字字型、shell／taskbar 圖示及升級捷徑
- 使用者滑鼠／觸控筆跡的 top1／top10；模型筆跡重播不能代填
- 混合 DPI、多螢幕、RDP／休眠、Explorer 重啟、全域熱鍵及外部程式貼上
- 完整相依授權審查／簽章；目前 VM LeakSanitizer 相容性未建立通過結果

筆畫字庫含龘，但缺少內／吳／戶／辦／嚐等常用字，也不含尞／𠮷／𠀋。剪貼簿可保留擴充字不代表模型能辨出。既有 OCR 在 QThread 中永久不返回仍無法安全強制中止；手寫隔離不改變此限制。

歷史：[rc.2說明](RELEASE_1_7_0_RC2.md)、[OCR升級驗證](OCR_UPGRADE_20261006.md)。新候選維持 prerelease，不宣稱乾淨 Windows 或真人手寫全面驗收完成。

## 2026-10-15 整合狀態

- 候選協議v2、每路Top30、raw／bbox來源與rank去重、可展開／捲動／鍵盤候選UI
- 手動且可取消的 Small rec 影像子程序；來源碼早期分流與 bounded IPC，frozen驗證新門檻已加入
- 已封存1536份訓練重播／幾何擾動輸入；Top1／10／30及raster對照僅定位根因，不是人體筆跡準確率
- 提交 741303d1a4805dd02b3f5569711368161404828e 的 Windows／Linux 驗證、Windows EXE 打包、封鎖網路壓力測試與公開候選上傳已通過，見 [Actions 38693217612](https://github.com/maotai11/desktop-ocr-tool/actions/runs/38693217612)。該檔案仍標記 rc.1，不覆蓋舊 Release
- rc.2 新版本與同次公開檔案交接流程仍需重建重驗；乾淨 Windows 與真人獨立樣本待驗收
- raw前10保留避免反向筆畫回歸，但小字偏角落Top1排序尚未解決；OCR字表含缺字不等於已補準

完整證據範圍見 [手寫改善紀錄](HANDWRITING_IMPROVEMENT_20261015.md)。
