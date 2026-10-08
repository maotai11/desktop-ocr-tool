# v1.7.0-rc.2：待確認自動複製與可選清理

修正使用者回報的「待確認時沒有自動複製」。rc.1 的 `Pipeline.persisted` 對 `needs_review` 只顯示提示，跳過寫入剪貼簿，因此外部貼上仍可能得到上一筆內容。rc.2 讓最新且有效的候選可複製，保留 `needs_review`，不移除人工確認／候選比較機制。

## 複製清理

設定 → 剪貼簿的三個獨立勾選預設全關閉：

- 逗號：`,，`
- 撇號：`'‘’＇`
- 換行：CR、LF、NEL、Unicode line／paragraph separator

只清理自動複製、歷史複製及貼上最後一筆的輸出。原始 OCR、後處理文字、候選、人工校正、歷史與匯出不變；負號、小數點、其他符號與空格保留。這是字元清理，一般文字中的指定字元也會移除。換行合併會把同筆的不同列接起來，必須由使用者勾選；批量仍保留不同記錄的分隔。編輯框原生 Ctrl+C 保留選取原文。

## 驗證

本輪功能修正的 Linux／offscreen 完整測試為588 passed、10 PowerShell-only skipped、0 failed；其中33項新測試涵蓋複製清理、SQLite文字／provenance不變、狀態保留、過時／刪除／人工修改防覆蓋、焦點與真實設定save/reload。source OCR／Qt／SQLite與app lifecycle在已驗證的Linux socket-deny下通過，不代表Windows斷網驗收。

發布流程仍只允許指定修正分支和 `publish-prerelease: v1.7.0-rc.2` marker，在同一commit通過雙平台tests、source/frozen probes、Windows outbound firewall block、兩個模型各500次推論與EXE／ZIP／model／source hashes後發布。實際成品驗證見 [Release](https://github.com/maotai11/desktop-ocr-tool/releases/tag/v1.7.0-rc.2) 的 validation metadata ZIP與SHA256SUMS，不沿用rc.1資產。

## 尚未完成

乾淨離線Windows、外部程式Ctrl+V、全域熱鍵／SendInput、混合DPI／多螢幕與現場文字正確率仍需實機驗收。`clean_machine_verified=false` 與 `mixed_dpi_gate=NOT_RUN` 必須保留，因此只發布為prerelease。沒有更換OCR模型、增加依賴、使用外部OCR或打包私人截圖／資料庫。
