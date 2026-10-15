# InkPick 拾字筆

Windows 10（1809 以上）／11 x64 離線文字工具：框選 OCR、手寫選字、複製與本機歷史。沿用原本的 desktop-ocr-tool 儲存庫及資料格式。

既有版本標記為 **1.8.0-rc.1 預發布候選**。本工作樹新增的候選／介面與影像補辨尚未產生新版 Windows 成品，不能以舊 EXE 的驗證結果代替。成品內含 Python、Qt、OCR 模型及手寫引擎，不使用 Windows 手寫語言套件或首次下載。請從本儲存庫的 [Release](https://github.com/maotai11/desktop-ocr-tool/releases) 取得成品並核對 SHA256；實際驗證以同版本 validation metadata 為準。Windows CI 不是乾淨使用者電腦，未完成的門檻見 [驗證狀態](docs/VALIDATION_STATUS.md)。

## 使用

1. 解壓 ZIP 到可寫入的資料夾，執行 InkPick EXE，不需要管理員權限
2. Ctrl+Shift+O 框選文字；結果保存在本機歷史，可編輯、搜尋及匯出
3. 點浮動窗或系統匣的「手寫」，在方框內寫一個字
4. 停筆後顯示候選，點選正確的字即可複製，再自行 Ctrl+V。新工作樹每路取 30 字：原始筆畫與置中筆畫去重後最多 60 字，保留原始前 10 排序；其餘可捲動或展開
5. Tab／方向鍵移動候選，Enter／空白鍵複製；Ctrl+Z 撤銷一筆，Escape 關閉
6. 找不到時可手動按「影像補辨」，以內含 Small OCR 對目前筆跡補充最多兩個單字候選；再按一次可取消。補辨仍需人工選字
7. 寫錯可用「撤銷一筆」或「清除」，也可按「辨識」重試

手寫複製保留所選原字，不經簡繁轉換或 OCR 清理，不直接向其他程式輸入。較早開始的 OCR 仍保存結果，但不能覆蓋稍後在拾字筆選字或從歷史手動複製的文字；下一次主動框選或重辨可照常自動複製。

## 字表與限制

- 手寫使用 Zinnia 筆畫辨識及固定 Tegaki 繁體模型，共 11,853 字
- 筆畫字庫含「龘」，但缺少「內、吳、戶、辦、嚐」等常用字，也不含「尞、𠮷、𠀋」。不能把缺字限制只理解成罕字
- 影像補辨使用字表不同的 PP-OCRv6 Small，能輸出哪些字不等於能把你的筆跡辨準；空白／多字輸出不拆成猜測單字，不做簡繁轉換
- 候選來源可由提示／輔助使用名稱查閱；原始與置中路保留各自排名，不把兩路原始分數加成機率。原始前 10 仍可能因小字或偏角落而排錯，排序品質仍待真人驗證
- 筆順、連筆及滑鼠寫法可能影響結果。影像補辨只在你按下時執行，完成後退出，不會每次停筆另載 OCR
- 原始模型筆跡重播只是功能測試，不能當作真人手寫準確率
- 既有 OCR 仍提供 PP-OCRv6 Small／Medium；它們與手寫模型的字表不同

姓名、日期、金額及帳號請覆核。未收錄的字不猜字、不以全域替換補上。

## 升級與資料

先關閉舊程式並備份原有 config／data。將新版 EXE 放回原來的可寫入資料夾，可繼續讀取同一份設定、歷史及圖片；不要以新包覆蓋自己的資料。若換資料夾，請一併移轉原有 config／data。舊捷徑或自動啟動若指向舊 EXE，請更新捷徑，或在新版設定重新套用自動啟動。

名稱及圖示改為 InkPick 拾字筆；原有資料路徑、DESKTOP_OCR_HOME／DESKTOP_OCR_VERSION、單實例鎖、剪貼簿識別及自動啟動登錄名稱保持相容。新安裝預設不監聽剪貼簿，既有設定保留原選擇。

設定中的逗號、撇號及換行清理仍預設關閉，只作用於 OCR／歷史的複製輸出，不改原始內容；手寫選字保留原字。移除換行會接起同筆的不同列，只在確定需要時啟用。

歷史、截圖及匯出皆為明文，沒有自動到期清除。應用程式本身不傳送圖片或手寫筆跡；若開啟 Windows／第三方剪貼簿同步，所複製的文字可能由該服務傳送。

## 開發與建置

Windows 驗證通過後另製作公開候選檔案：保留 EXE、授權與必要模型原始碼，排除原始診斷報告及建置路徑。公開 ZIP 與內部候選 ZIP 分別核對，這個步驟不會自動發布 Release。

只在開發／建置機安裝套件。Python 3.12；Windows 使用已安裝的 Microsoft x64 C++ 編譯器及 Release `/MT` 靜態 CRT。最低 Windows 版本依固定 [Qt 6.11 支援平台](https://doc.qt.io/qtforpython-6.11/overviews/qtdoc-windows.html) 為 Windows 10 1809。目標電腦不執行這些步驟。

```sh
python -m pip install -r requirements-dev.txt
python scripts/prepare_models.py
python scripts/prepare_handwriting_model.py
python scripts/build_handwriting_helper.py
python scripts/probe_handwriting_helper.py --report artifacts/handwriting-native-probe.json
python -m pytest -q
python src/main.py --self-test source-selftest.json
python src/main.py --smoke-app source-app-smoke.json
python src/main.py --self-test-handwriting source-handwriting.json
# Windows only:
python scripts/build.py
```

Linux 無桌面測試可設 QT_QPA_PLATFORM=offscreen；它不代替原生 Windows UI、觸控或 DPI 驗收。下載程式只在建置階段使用固定官方來源及 SHA256，應用程式沒有執行期下載路徑。

本次工程／介面證據與未完成項目：[手寫改善紀錄](docs/HANDWRITING_IMPROVEMENT_20261015.md)。

技術細節：[封裝與同一 EXE 驗證](PACKAGING_NOTES.md)、[手寫程序邊界](native/handwriting/README.md)、[第三方授權](THIRD_PARTY_NOTICES.md)、[CHANGELOG](CHANGELOG.md)。既有 OCR 比較保留在 [升級報告](docs/OCR_UPGRADE_20261006.md)。
