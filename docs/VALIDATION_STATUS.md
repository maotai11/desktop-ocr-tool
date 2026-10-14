# InkPick 1.8.0-rc.1 驗收界線

實際完成結果以 Release 同一 EXE／ZIP SHA256、validation metadata、BUILD_MANIFEST 及 SOURCE_MANIFEST 為準；不能沿用 rc.2 或其他 commit 的結果。

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

手寫含龘，不含尞／𠮷／𠀋。剪貼簿可保留擴充字不代表模型能辨出。既有 OCR 在 QThread 中永久不返回仍無法安全強制中止；手寫隔離不改變此限制。

歷史：[rc.2說明](RELEASE_1_7_0_RC2.md)、[OCR升級驗證](OCR_UPGRADE_20261006.md)。新候選維持 prerelease，不宣稱乾淨 Windows 或真人手寫全面驗收完成。
