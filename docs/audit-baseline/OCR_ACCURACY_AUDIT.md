# OCR Accuracy / Taiwan Traditional Chinese Forensics

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## 實際 pipeline 與 stage boundary

Capture：MSS BGRA ndarray→BGR view→PIL RGBcopy→PNG/thumbnail→DBinsert→cv2.imread BGR→upscale_if_small短邊minimum960→RapidOCR內部max_side2000/min_side30及detresize短邊736→DB detector→crop/cls→CTC rec→text_score=.5 filter→app secondpass判斷。Secondpass固定CLAHE/fastNlMeans/optionalOtsu，再同引擎整張重辨，結果append。之後每個box zhconv('zh-hant')→sort_boxes_and_merge→fallback條件與provider confidence比較→OcrWorker DTO→SQLite；raw cleanup與persisted ack順序見D02。

沒有 active deskew/Sauvola/shadow/customdictionary/context stage；一階rawinput不跑前處理，二階不看preprocessing.*。傳統／臺灣地區轉換只有一般zh-hant，沒有zh-TW術語domainpack。

## 真實 loaded model／字表

CONFIRMED：OcrEngine.load參數為RapidOCR()，模型來源是 installed rapidocr_onnxruntime，不是cfg paths；ONNX session `_model_path`、SHA256、output shape、decoderchars留在 baseline-models.json。三個repo檔案此版本hash恰好相同；classmodel實際 mobile_v2.0（repo名稱v4不能證版本）。

| Runtime | Detector identity | Recognizer identity | Decoder symbols | Traditional / rare coverage |
|---|---|---|---:|---|
| Legacy1.4.4 | ch_PP-OCRv4_det_infer，sha d2a772… | ch_PP-OCRv4_rec_infer，sha48fc40… | 6625（含blank、space） | 本GT136 unique字有23 directmissing；其中16可簡體單字roundtrip，7不可；不等於23個final皆無法產生 |
| RapidOCR3.9.2 v5mobile | 4d97c4… | 5825fc… | 18385（specialincluded） | GT缺尞、𠀋、𠮷；其餘有symbol不保證會認對 |
| RapidOCR3.9.2 v6small | snapshot完整hash | snapshot完整hash | 18710（specialincluded） | 同三字missing；不是完整台灣字庫認證 |
| NativePaddle3.7 v5mobile | explicitlocalinfer YAML/JSON/pdiparams | explicitlocalinfer YAML/JSON/pdiparams | metadata字表另可extract | app adapter未實載這個指定實驗模型，不將benchmark與app世代混同 |
| CnOCR2.3.3 | 未native載入 | 未native載入 | UNKNOWN | core/benchmark環境未安裝；provideravailablefalse，不虛构CER |

CnOCR核對：`cnstd-model-name-search.json` 搜尋完整 wheel，設定的 `ch_PP-OCRv5_det` 確實存在於 `cnstd/ppocr/consts.py`；只查 top-level consts 得出的不存在判斷已撤銷。此項不列 issue，未安裝／未 native 實載仍為 UNKNOWN。`provider-contracts.json` 實際執行 upstream resolver（不建立 session）得到 Chinese Traditional 預設 v6 medium det/rec；這是模型名稱解析證據，不是 app 已載入證據。

6625包含兩special符號，原始metadata6623 entries；metadata entries是否均為單一Unicode字由snapshot可逐一驗，不能當6623個台灣繁中字。simplified签在single-char zh-hant轉換存在籤/簽ambiguity；詞組可改轉換選擇，所以singlecharroundtripmissing不證明context完全不可達。罕字𠮷/𠀋沒有模型symbol，沒有activecontextcorrection，不能靠有dictionaries檔案補出。

## Ground truth 與量測方法

36張＝12 domains×10/14/24px，NotoSansCJKtc-Regular；manifest每張GT文字/rect/SHA256與fontSHA。font-coverage確認所有GT Unicode在fontcmap，避免tofu把不支援glyph誤作模型失敗。各domain一般繁中、高筆畫、己已巳/未末/土士/日曰、罕字、人名用字、会計、稅務/2.11%、發票/金額、勞健保、法律/公司型態、台灣地名、mixedAPI/date/unit、正確文本負例。字體單一、單行、乾淨白底，無真實銀行/發票/醫療/使用者PII；不是代表性驗收集。

CER=(S+D+I)/N；MissRate=D/N、Substitution=S/N、Insertion=I/N。固定Levenshtein ties優先substitution→deletion→insertion。Whitespace Error Rate另以full-textalignment只計涉及Unicode whitespace的edit/N；原始suite中的whitespace-only stream忽略位置，**不得用其單獨作修正成效**，report重新計location-awarecount，且不是worderrorrate。

Detection：stage-only保留未recognitionfilterboxes，与每張單行GTrect比較intersection/GTarea>=.5。這是**line coverage recall診斷**，不是標準polygonIoU recall、glyphrecall或全部字完整率。fragmentedboxes、多欄、表格需下一版polygon GT；不可用overallCER代替detector recall。Recognition：recognition-stage對相同已知GT tightcrop加2pxwhiteborder，三模型直接recognizer，bypassdetection，保留raw與zh-hant兩種CER，確保漏偵測與認错可分。

## A/B raw evidence

| Experiment | N | CER | D/N | S/N | I/N | Line coverage recall | Full-string exact | p50 OCR ms | p95 ms | p99 ms | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| controlled-v4 | 36 | 0.2058 | 0.0674 | 0.1020 | 0.0364 | 0.861 | 0.1111 | 1334.0 | 2205.583 | 2660.521 | 997.203 |
| matched-v5 | 36 | 0.9381 | 0.9162 | 0.0219 | 0.0000 | 0.028 | 0.0278 | 1522.1 | 2164.827 | 2369.030 | 1143.992 |
| native-scale-v5 | 36 | 0.0710 | 0.0182 | 0.0528 | 0.0000 | 0.972 | 0.4444 | 94.9 | 155.340 | 181.797 | 315.910 |
| aligned-v6 | 36 | 0.1148 | 0.0565 | 0.0474 | 0.0109 | 0.889 | 0.4722 | 2424.3 | 3548.782 | 4325.653 | 1063.617 |
| production-default | 3 | 0.2381 | 0.1190 | 0.1190 | 0.0000 | 1.000 | 0.0000 | 2210.6 | UNKNOWN (N=3) | UNKNOWN (N=3) | 977.781 |
| second-pass | 12 | 0.4558 | 0.1088 | 0.2585 | 0.0884 | 1.000 | 0.0000 | 1696.7 | UNKNOWN (N=12) | UNKNOWN (N=12) | 1059.059 |
| native-paddle-v5 | 36 | 0.0765 | 0.0200 | 0.0546 | 0.0018 | UNKNOWN | 0.4444 | 198.2 | UNKNOWN | UNKNOWN | 642.027 |

`controlled-v4`＝產品first-pass＋twoORTthreads；`production-default`僅3張defaultthread，不能以3樣本估p99尾延遲；N<20 的 p95/p99 直接標 UNKNOWN；36 張亦只為診斷經驗值，非可靠 release tail gate。`aligned-v6`與v4同appresize/zh-hant/merge邊界；vendorintermediates仍應記錄。`matched-v5`只對齊正規化，沿v4短邊736偵測策略造成大量漏偵測；不能稱v5模型更差。`native-scale-v5`再依v5 inference.yml的longside方向調整max-side，明顯差異是resizepipeline，不是只換weights。NativePaddle使用原圖與其YAML預處理，非app短邊upscale；同GT可看行為但latency不是完全相同pipeline。

此suite v6結果較低CER只適用36張、明確參數/版本；不是Engine migration批准，不代表罕字、真實發票、多欄、手寫或Windowspackage已提升。v5/v6 directvocab較大不保證allTCaccuracy。Paddle原生能出字但repo adapter36/36mapping空done；actualrecognize(PIL)被reader拒絕，空done。資料及log完整保留。

### Recognition-only（與Detection分離）

| Engine | N | raw CER | zh-hant CER | converted exact | Whitespace edits / target chars |
|---|---:|---:|---:|---:|---:|
| v4 | 36 | 0.2168 | 0.1676 | 0.1944 | 8/549 |
| v5 | 36 | 0.0710 | 0.0619 | 0.5278 | 5/549 |
| v6 | 36 | 0.0474 | 0.0419 | 0.5833 | 5/549 |

### Whitespace-error evidence by pipeline

| Experiment | location-aware whitespace edits / target chars |
|---|---|
| controlled-v4 | 24/549 |
| native-scale-v5 | 9/549 |
| aligned-v6 | 19/549 |
| second-pass | 1/147 |

## 語言感知 whitespace policy（設計，未實作）

必先空間fusion/line/column clustering；spacing不負責去重。preserve內部literalwhitespace，跨box邊界以mediancharacterheight、boxgap、languageclass判斷；語言policy＋geometryconflict標review，禁止allremove。

| Boundary / format | Proposed decision | GT例與負例 |
|---|---|---|
| CJK→CJK | continuoussamecolumn小gap無space；大gap欄位保留tab/newline或explicitseparator | 營業稅、己所不欲；兩欄「借方」「貸方」不能黏 |
| CJK↔Latin | 原文有分隔／moderategap保留一space；embedded名稱依GT／context | 所得稅 API；iPhone手機需按source不是alwaysspace |
| Latin↔Latin | word boundary/gap one space；hyphen/email/token不得被分拆 | hello world；AB-12345678、API_key |
| number↔unit | unitpack定義：SI可保留10 kg；%、℃等compact與sourceconsistent | 10 kg、10%、25℃；不能將帳號或10公斤亂分 |
| punctuation | CJK標點前不加space、後以source/column；Latin逗號後可保留space | 借：租金費用、hello, world |
| brackets | opening後/closing前不加space；跨欄或math區分 | （台灣）、(API key)、法律款項括弧 |
| percentage | digit與%compact，保留前後完整句spacing | 2.11%、-5.00%；百分點不是比例字串替換 |
| dates | separator不加字間space；ROC/Gregorian格式不擅自換年月 | 115/10/02、2026-10-02 |
| money | currency前綴/千分comma/decimal做token；對齊columns另存layout | NT$1,234.00、(1,234)、-1,234.00 |
| accounting/tax | 保留sign、括號負數、借/貸欄及稅率；不是單純中文段落 | 借：租金費用；借貸雙欄需line/column GT |

現行10個whitespace fixture只有3pass；groundtruthtests在suite.pyprobes與fixtureJSON，這是錯誤存在證據，未加入產品tests改碼。下一階段分literal/空間column/format兩層tests，fixgate要求全部精確equal，同時英文/帳號/金額負例不退化。

## Spatial result fusion（設計，未實作）

每candidate帶原圖polygon、pass/provider/modelhash、confidence、rawtext、transforms。先inverse scale與rotation map；建same-line collision graph，IoU/GTcoverage/center-distance/readingdirection作幾何證據，threshold須由標注overlapfixture選。相同glyph同位置只留局部winner；相同text異位置必須保留。兩box不同分割粒度先span alignment而非全域NMS；同位置不同glyph保留alternatives與review。confidence依engine/pass校準，不跨模型裸比。merged選擇與rejectedcandidate均入provenance；然後才spacing。

measuregate：duplicatebox/textsameglyph、same-linecollision、partialoverlap、nestedbox、跨行、旋轉、表格、合法「人人」；chosenCER、detrecall、wrong-suppressionrate、p95/RSS。不能宣称sort_boxes_and_merge自然去重。

## Taiwan lexicon / context correction（設計，未實作）

| Layer | Responsibility / evidence boundary |
|---|---|
| Character vocabulary | CTC可直接输出symbol，coverage可查modelmetadata；不是domain詞典 |
| Recognition model | 字形→序列；要GTcropCER，不以字表大小替代 |
| Domain lexicon | 有版本/來源/授權的pack：一般繁中、台灣地名、人名常見字、会計、稅務、發票、勞健保、法律、銀行金融、公司、專名 |
| Confusion pairs | 鬱/郁、籤/簽、己/已/巳、未/末、土/士候選；是candidateedge，不是replace命令 |
| Context correction | 依窗口、語法、domain/date/currency/legalidentifier、空間 alternatives 評分；不確定只suggest |
| User dictionary | opt-in詞組/公司/術語，namespace、priority、version及衝突處理；不可覆蓋所有context |
| Taiwan terminology | 獨立 localevariant policy；台/臺、人名／公司註冊原字保持source，不強制變體統一 |

紀錄raw、normalized、suggested、accepted、rule/packversion，可撤回。负例：己所不欲、自己、已經申報、巳時；「己→已」不無條件套用。詞庫不能回復detector根本漏掉的glyph；不能以context猜罕名當已辨識。評估acceptedwrongcorrectionrate、suggestprecision、baselineCER、domain/rarecoverages；未經人工審核pack不可自動上線。

## 下一個 Ground Truth / Quality Gate

實際1280/4K/不同DPI螢幕crop、低對比/複雜背景、多欄財務表、台灣稅單/發票/人名公司、rare_extension_B、字高6/8/10/12/14/18px、fontweight/anti-aliasing、極端比例、正確text負例。人工標polygon與每line全文雙人核對，分held-out與tuning；不得把36合成句調參後宣稱泛化。測reportCER/S/D/I、literal/canonical兩種、whiteerror、line/polygondetrecall、croprecognition、latencypercentiles與RSS；defaultsecondpass/fallbackrouting另做end-to-end。

## Current dependency research（2026-10-02）

官方PyPI記錄legacy rapidocr-onnxruntime最新1.4.4於2025-01-17；current rapidocr3.9.2於2026-07-21。此時間差證明release線不同，不能單憑此稱legacy已停止所有維護。RapidAI currentdocs/release主推rapidocr，提供v5/v6及多runtime；current API returndataclasses／TextRecInput不同於legacytuple，defaultmodel也不同。

比較需包括本報告GTcoverage/CER、RSS/CPU/load、wheeldata/EXEclosure、ONNXoffline、本機cache下載、PyInstallerhooks、runtimeidentity/whitespace/fusion/API變更。程式package license與每個model權利分開；repo沒有完整modellicense manifest，因此commercialRedistribution/modelusage授權仍UNKNOWN。外部模型能力宣稱不取代本地GT。

Primary sources：[RapidAI releases](https://github.com/RapidAI/RapidOCR/releases)、[官方安裝](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/install/)、[官方模型list](https://rapidai.github.io/RapidOCRDocs/main/model_list/)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)、[CnOCR](https://github.com/breezedeus/CnOCR)、[PyPI registry](https://pypi.org/pypi/rapidocr-onnxruntime/json)。精確版本/時間/檔案hash見dependency-registry.json。
