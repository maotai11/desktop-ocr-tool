# InkPick 拾字筆

Windows 10（1809 以上）／Windows 11 的 64 位元離線文字工具。可框選螢幕辨識文字、手寫單字找候選，也能匯入 PDF／圖片，把固定位置的欄位逐頁填入文字模板，最後複製或匯出 TXT。

本版為 **1.8.0-rc.5 候選版**，延續文件套版、螢幕擷取、複製模式、手寫、歷史與檔案位置功能。EXE 包含 Python、Qt、PDFium、兩組 OCR 模型及手寫引擎；使用電腦不需安裝 Python、pip、PDF 閱讀器或 Windows 手寫語言套件，也不需連線下載模型。

這次修正設定下拉選單的文字對比與手寫時畫布跳動，並在文件套版補上「清除框選」「清空文件」及可見的縮放、拖曳控制。

## 下載與第一次執行

從 [v1.8.0-rc.5 Release](https://github.com/maotai11/desktop-ocr-tool/releases/tag/v1.8.0-rc.5) 下載：

| 檔案 | 用途 |
| --- | --- |
| [InkPick-v1.8.0-rc.5.zip](https://github.com/maotai11/desktop-ocr-tool/releases/download/v1.8.0-rc.5/InkPick-v1.8.0-rc.5.zip) | 建議使用。包含 EXE、離線操作說明、文件套版圖文說明、授權與手寫模型來源 |
| [InkPick-v1.8.0-rc.5.exe](https://github.com/maotai11/desktop-ocr-tool/releases/download/v1.8.0-rc.5/InkPick-v1.8.0-rc.5.exe) | 同版單一執行檔；PDF 引擎、模型與執行環境已包在裡面 |
| [InkPick-v1.8.0-rc.5-README.txt](https://github.com/maotai11/desktop-ocr-tool/releases/download/v1.8.0-rc.5/InkPick-v1.8.0-rc.5-README.txt) | 可單獨下載的離線操作說明，與 ZIP 內 `OFFLINE_README.txt` 內容相同 |
| InkPick-v1.8.0-rc.5-SHA256SUMS.txt | 核對同版下載檔案的 SHA256 |
| InkPick-v1.8.0-rc.5-validation-metadata.zip | 同次建置的去識別驗證摘要，不是執行必需檔案 |

1. 在可上網的電腦下載 ZIP，再用隨身碟帶到離線電腦。
2. 解壓到自己有寫入權限的資料夾，例如 `D:\Tools\InkPick`。不要直接從 ZIP 裡執行，也不要放在受保護的 Program Files。
3. 執行 `InkPick-v1.8.0-rc.5.exe`，不需管理員權限。預設縮到右下角系統匣，點拾字筆圖示開啟浮動窗。
4. 首次開啟會解開內含資源並載入模型。浮動窗顯示「可擷取文字」後，即可使用。
5. 結束程式請用系統匣的結束選項，讓尚未完成的辨識與儲存先結束。

核對檔案可用 PowerShell：`Get-FileHash .\InkPick-v1.8.0-rc.5.exe -Algorithm SHA256`，與同版 SHA256SUMS 的 EXE 那一列比對。ZIP 內的 `OFFLINE_README.txt` 與 `README.txt` 是相同的完整文字說明，可直接用記事本閱讀。

## 文件套版：PDF／圖片逐頁填入文字

適合同一版型的通知單、表格或掃描圖片。例如每頁都要取出姓名與金額，組成固定文字：

1. 點浮動窗「文件套版」，或從系統匣選「文件套版（PDF／圖片）」。
2. 點「匯入 PDF／圖片」。預設**每個檔案上限 100 MB**，可在視窗調整為 1–1024 MB；可一次選多個檔案。
3. 在第一頁的「框選欄位」分頁選「辨識1」，拖曳框出第一段文字；按「新增欄位」建立「辨識2」，再框第二段。
4. 切到「文字模板」，輸入 `文字｛辨識1}文字測試{辨識2}`。也可把欄位改名為「姓名」「金額」，按「重新命名」後使用 `{姓名}`、`{金額}`；模板中的舊名稱會同步更新。
5. 按「開始辨識」。在「逐頁核對」檢查結果；雙擊欄位或按 F2 可修改文字，包含多行內容。
6. 檢查「TXT 預覽」，按「複製結果」或「匯出 TXT」。每頁各產生一份文字，頁與頁額外用空行分隔；TXT 使用 UTF-8（含 BOM），可用 Windows 記事本開啟。

若某頁的「辨識1」「辨識2」分別是「王小明」「1,234」，上述模板會輸出 `文字王小明文字測試1,234`。模板的固定文字、標點、空格與換行會保留，不套用螢幕 OCR 的逗號／換行清理。

**後面沒重選，就沿用第一頁。** 第二頁位置不同時，切到第二頁，在「只改這一頁」範圍重新框選需要調整的欄位；第三頁未調整時仍用第一頁的位置。按「此頁套用第一頁」可清除當前頁面的所有例外位置。不同圖片尺寸依頁面比例套用，但不會自動追蹤版面移動的欄位。

框錯位置時，選取欄位再按「清除框選」：「所有頁共用」會清掉這個欄位的共用框與各頁例外；「只改這一頁」只清當前頁，該頁不會自動改用第一頁的框，需重新框選或按「此頁套用第一頁」。要換一批文件，可按「清空文件」並確認；它會清除匯入頁面、全部框選位置及結果，保留欄位名稱與文字模板，不刪原檔、不改剪貼簿。

預覽上方的「−」「＋」可縮放，「100%」切回百分之百，「適合視窗」顯示整頁；放大後開啟「拖曳移動」即可拖動頁面，關閉後繼續框選。也支援 Ctrl＋滾輪縮放及滑鼠中鍵拖動。

支援 PDF、PNG、JPEG、BMP、TIFF、WebP，每批最多 500 頁、32 個命名欄位。一般已填 PDF 表單可顯示內容；加密 PDF 請先解除密碼，XFA 動態表單請先列印或另存為一般 PDF。原始檔只讀，不會自動加入截圖歷史或另存副本。欄位、模板與結果只保留在本次程式執行中，需要留檔請先匯出 TXT。

失敗或尚未完成的欄位會阻止匯出，請先重辨或修正；匯入、辨識、停止與關閉視窗都不會自動複製。更多位置調整、錯誤處理與操作圖見 [文件套版說明](docs/DOCUMENT_TEMPLATE.md)。

![文件套版的文字模板與 TXT 預覽](docs/ui/document-template.png)

## 從螢幕擷取文字

### 預設：辨識後自動複製全文

1. 點浮動窗「擷取文字」，或按 **Ctrl+Shift+O**。
2. 在螢幕上拖曳框出要辨識的範圍；Esc 可取消。
3. 等待辨識完成，這次範圍內的全文會自動複製。
4. 切回目標程式，按 **Ctrl+V** 貼上。

辨識結果與截圖照原設定保存在本機歷史。失敗、空白或已過期的辨識不會清空剪貼簿。若辨識期間已從拾字筆手寫或歷史手動複製其他內容，較早的結果只存入歷史，不覆蓋這次手動複製。

### 只想複製其中幾段

開啟浮動窗右上角設定，進入 **剪貼簿 → 辨識完成後 → 先選文字再複製**，按確定。儲存後立即生效，不需重啟。

第一步仍是框選螢幕、再進行 OCR；只改變辨識完成後的處理：

1. 辨識完成後開啟「選取要複製的文字」，預設未選取。
2. 在左側原圖點選文字框，或拖曳框選多段；Ctrl 拖曳可加選。也能在右側逐段勾選，空白鍵切換勾選。
3. 下方「複製內容」可修正文字，或反白其中一小段。按「複製」或 Ctrl+Enter，才會寫入剪貼簿。
4. 「全選」可複製整份結果；「稍後再選」、Esc 或關閉視窗都不複製。

只複製一段不會刪掉歷史中的其餘內容。取消後可從歷史開啟編輯器，再反白／複製需要的文字；沒有原圖時仍可勾選文字。選取與預覽修改不會將整份結果標為已核對。

要恢復原操作，將選項改回 **自動複製全部文字**。新安裝與沒有這個設定的舊設定檔，預設皆為全文自動複製。

![辨識後選取文字](docs/ui/ocr-selection.png)

![設定中的複製模式](docs/ui/copy-settings.png)

## 截圖、手寫與歷史

- **截圖**：點「截圖」或按 Ctrl+Shift+S。是否接著執行 OCR，依「設定 → 擷取 → 截圖後自動執行 OCR」。
- **手寫單字**：點「手寫」，在方格寫一個字；停筆後顯示候選。點候選字就複製原字，再自行 Ctrl+V。「撤銷一筆」、Ctrl+Z 或「清除」可重寫。
- **穩定書寫**：候選區清空或更新時保留高度，避免畫布突然變大、讓同一筆的起點與終點移位；展開、收合及選字方式維持原操作。
- **更多候選**：原始與置中筆畫各取前 30 字，去重最多 60 字。「展開」後可捲動、用方向鍵移動及 Enter／空白鍵複製。
- **影像補辨**：手寫沒有需要的候選時，按「影像補辨」，另外用內含 OCR 增補最多兩個候選；再按一次可取消，不能保證補準缺字。
- **歷史紀錄**：浮動窗底部可開啟歷史視窗，搜尋、編輯、釘選、刪除、複製或匯出。浮動窗已移除搜尋框，Ctrl+Shift+Q 直接開啟歷史搜尋。

歷史視窗的「匯出」支援 TXT、CSV（Excel 相容）、JSON 與 ZIP。CSV 使用 UTF-8 BOM，包含文字、來源、OCR 狀態、信心分數等欄位，預設存入 `data/exports`；需要手動匯出才會產生。文件套版目前提供 TXT，Release 不會夾帶使用者匯出的 CSV。

| 快捷鍵 | 動作 |
| --- | --- |
| Ctrl+Shift+O | 框選並辨識文字 |
| Ctrl+Shift+S | 框選截圖 |
| Ctrl+Shift+Space | 顯示／隱藏浮動窗 |
| Ctrl+Shift+M | 開啟歷史視窗 |
| Ctrl+Shift+Q | 開啟歷史搜尋並聚焦搜尋欄 |
| Ctrl+Shift+V | 貼上最近一筆紀錄的文字 |

## 暫存、附件、產生的檔案與 Log

點浮動窗底部 **「檔案位置」** 即可開啟下列位置。「截圖與附件」及「紀錄與設定」有子選單；不存在的位置會停用。選單只開資料夾，不會刪檔，並依目前實際使用的路徑開啟。

| 選項 | 預設位置／內容 |
| --- | --- |
| 截圖 | EXE 同層 `data\captures`；原始截圖 |
| 標註附件 | `data\annotations`；圖片編輯與標註產物 |
| 縮圖 | `data\thumbnails`；歷史清單的圖片預覽 |
| 匯出檔案 | `data\exports`；從歷史匯出的檔案 |
| 歷史資料 | `data`；`app.db` 與執行中的 SQLite WAL／SHM 暫存 |
| 設定檔案 | EXE 同層 `config`；`settings.json`、備份及儲存中的暫存檔 |
| 執行記錄 (Log) | EXE 同層 `logs`；`app.log` 及按日期輪替的舊檔，最多保留 30 份舊 Log |
| 程式暫存 | 這次執行的 `%TEMP%\_MEI…`；EXE 解開的程式、模型與引擎資源，正常退出後自動清理 |
| 程式資料夾 | 目前使用的程式主資料夾 |

程式暫存與歷史資料不同：退出程式不會刪除 `data` 的截圖、附件、匯出或歷史，Log 也有獨立保留週期。請先退出程式再備份或移動資料，不要在執行中刪除 `_MEI…`、資料庫或附件。

自訂資料目錄只改變 `data` 所在位置，設定與 Log 仍跟隨程式根目錄。若曾設定 `DESKTOP_OCR_HOME`，則以該位置為根目錄。在另存對話框自行選到其他位置的檔案，仍留在當時選的路徑。

![檔案位置選單](docs/ui/file-locations.png)

## 升級、備份與隱私

先結束舊程式，備份原有 `config`、`data` 與需要保留的 `logs`，再將新版 EXE 放回原資料夾。若換資料夾，請一起移轉 `config`／`data`。舊捷徑與自動啟動若指向舊 EXE，請更新捷徑，或在新版設定重新套用自動啟動。

- 新安裝預設**關閉剪貼簿監看**，舊設定保留。需要保存外部複製的文字時，才到「設定 → 剪貼簿」啟用。
- 歷史、截圖、附件、匯出與 Log 都是未加密的本機檔案。不要把整份 `data`、`config` 或 `logs` 當成公開錯誤回報附件。
- OCR 與手寫不需上傳圖片或筆跡。若啟用 Windows／第三方剪貼簿同步，複製內容可能被該服務同步。
- 逗號、撇號、換行清理預設關閉，只影響複製輸出，不改歷史原文，也不作用於手寫候選原字。例如移除換行後，`甲\n乙` 會複製為 `甲乙`。
- 新版公開 ZIP 排除原始 JUnit、錯誤堆疊與絕對建置路徑；公開驗證摘要不帶使用者資料夾。Repo 歷史與較早 Release 的檢查結果見 [隱私檢查](https://github.com/maotai11/desktop-ocr-tool/blob/master/docs/PRIVACY_AUDIT.md)。

## 辨識限制與離線驗證範圍

OCR 有 PP-OCRv6 Small／Medium 辨字模型可選，兩者都已內含；Medium 較耗記憶體，不代表每字較準。請核對姓名、日期、金額與帳號。

筆畫字庫共 11,853 字，含「龘」，不含「尞、𠮷、𠀋」，也缺少「內、吳、戶、辦、嚐」等常用字。影像補辨使用另一套 OCR 字庫。候選排序與分數不是正確率，不會自動把缺字猜成其他字。

發布檢查對同次 Windows 建置的 EXE 執行 OCR、資料庫、手寫、PDF／圖片文件、啟閉與防火牆阻擋對外連線測試，並核對模型、PDFium、內嵌資源與成品雜湊。獨立資料夾、空白使用者資料與縮減 PATH 的測試用於檢查開發機依賴；仍不等於真實乾淨 Windows 10／11 的現場驗收。真人筆跡、觸控、混合 DPI 與多螢幕仍須實機核對，見 [驗證狀態](https://github.com/maotai11/desktop-ocr-tool/blob/master/docs/VALIDATION_STATUS.md)。

## 原始碼開發與建置

以下只在開發／建置機執行，離線使用電腦不需要做。Python 3.12；Windows 建置另需 Microsoft x64 C++ 編譯器，手寫 helper 使用 Release `/MT` 靜態 CRT。

```sh
python -m pip install -r requirements-dev.txt
python scripts/prepare_models.py
python scripts/prepare_handwriting_model.py
python scripts/build_handwriting_helper.py
python scripts/probe_handwriting_helper.py --report artifacts/handwriting-native-probe.json
python scripts/run_bounded_pytest.py --report artifacts/pytest-bounds.json --memory-mib 4096 --timeout 1800 -- -q
python src/main.py --self-test source-selftest.json
python src/main.py --smoke-app source-app-smoke.json
python src/main.py --self-test-handwriting source-handwriting.json
python src/main.py --self-test-handwriting-image source-handwriting-image.json
python src/main.py --self-test-documents source-documents.json
# Windows only:
python scripts/build.py
```

模型只在建置時從固定來源下載並核對 SHA256；應用程式不會在首次執行補下載。Linux 無桌面測試可設 `QT_QPA_PLATFORM=offscreen`。

技術文件：[封裝與驗證](https://github.com/maotai11/desktop-ocr-tool/blob/master/PACKAGING_NOTES.md)、[公開驗證摘要](https://github.com/maotai11/desktop-ocr-tool/blob/master/docs/PUBLIC_VALIDATION_METADATA.md)、[第三方授權](THIRD_PARTY_NOTICES.md)、[CHANGELOG](https://github.com/maotai11/desktop-ocr-tool/blob/master/CHANGELOG.md)。
