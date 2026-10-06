# 候選成品驗收清單

所有空白都代表 NOT_RUN；不得預勾。CI self-test 成功不能代填乾淨 VM 欄位。

- [ ] 記錄 commit、ZIP／EXE SHA256、OS build、CPU、RAM、可用磁碟。
- [ ] 新建 Windows 一般使用者環境，沒有 Python／pip／OCR 套件及模型快取。
- [ ] 在網路中斷狀態解壓、首次啟動、關閉、重新啟動。
- [ ] `Validate-Candidate.ps1`兩種probe通過；核對schema2、frozen/qwindows、baked version、EXE／source manifest／model hashes、SQLite、threads_stopped。該腳本不自動簽發clean-machine通過。
- [ ] 記錄真正沒有Python／pip／OCR快取的環境及實際斷網狀態；PATH找不到Python不足以證明未安裝。
- [ ] 框選 OCR、截圖、取消框選、熱鍵衝突、系統匣、搜尋、編輯空字串、標籤、批次刪除／匯出。
- [ ] 125/150/175/200% DPI，主／非主螢幕、負座標、mixed DPI、螢幕拔除。現有 primary-screen 限制未解除。
- [ ] 休眠／恢復、RDP、Explorer 重啟、Windows 登出／關機。
- [ ] 新設定剪貼簿監聽關閉；手動啟用與暫停；來源含假 Token 時檢查保存／刪除範圍。
- [ ] 人工校對→failed/empty重跑、untouched／dirty editor、兩視窗stale notes、重跑中再編輯／刪除／restore、obsolete acknowledgement及不保留原圖清理競態。
- [ ] 辨識／截圖／DB 寫入中退出，確認已接受工作及持久化結果；無 QThread destruction。
- [ ] readonly 目錄、磁碟滿、鎖住原圖、损壞模型、刪除模型、配置 JSON 損壞；失敗訊息與重試。
- [ ] CSV 公式樣本為文字、ZIP 無越界／重名覆寫；SQLite integrity_check／FTS 同步。
- [ ] 連續 100／500 次 OCR、4K、多視窗、頻繁剪貼簿，記錄 latency distribution／RSS／DB／WAL。
- [ ] 依 PID／程序路徑歸因網路事件；記錄目的地與可見協定。無封包內容則 payload 標 UNKNOWN。
- [ ] 現場台灣繁中 Ground Truth：Detection Recall 與 cropped Recognition CER 分開；包含罕字及正確文字負例。
- [ ] 對照 BUILD_MANIFEST 所列依賴、模型授權及 Windows native DLL；審查簽章／來源。

正式 Release gate：上述紀錄及已知限制經審查，且與要發佈的同一 artifact hash 一致。不得換 build 後沿用舊驗收。
