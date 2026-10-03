# Documentation Drift vs Current Master

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## Drift by actual source contract

以下每列以固定HEAD source或本次execution比對；不是只引用另一份doc來判定。OCRreport空欄屬待填模板，不稱造假成績。

| Document / location | Claim | Actual evidence | Status / issue |
|---|---|---|---|
| README:3,14 | 完全離線、不需連線、不上傳任何資料 | ORTinstalled1.30telemetry、optionalcoldmodeldownload，repo無optout/localdir | CONFIRMED契約gap；actualwirepayloadUNKNOWN，S02/B03 |
| README:11 | 去斜/去噪/對比/陰影/Sauvola完整pipeline | activeonlysecondCLAHE/denoise/Otsu；deskew/shadow/Sauvolaorphan | CONFIRMED O05/X02 |
| README:12 | fallbackPP-OCRv5Mobile | provider未modelgeneration/type/device，nativeadapterPIL/schema损坏 | CONFIRMED O08/O09 |
| README:36 nearby | 安裝後勾啟用（optional預設不需） | defaultenableTrue/secondaryv5；不存在套件availableFalse，不等defaultfalse | CONFIRMED F03 |
| README:44 | python main.py | rootmain.py不存在；run.bat→python src/main.py | CONFIRMED X01 |
| README:47 | 首次60s載模型 | 本次Linuxcontrolledload1.5–2.7s、WincoldUNKNOWN | 此量化宣稱無HEADruntime證據，不能換填Linux值 |
| README:53 | CtrlShiftF全螢幕OCR | callbackfullscreen只save，onitemsaved僅region_ocr排OCR | CONFIRMED F07 |
| READMEfallback設定路徑 | 第二引擎(HandwritingOCR)組、greendiagnostic可用 | 新UI有兩套組；greendiagnostic僅import，不checkmodel/schema | CONFIRMED F02–F04/O08 |
| README:74–78 | artifacts/DesktopOCRTool.spec，150–200MB | spec不tracked、actualscriptsbuildonefile；cleanWinsizeUNKNOWN | CONFIRMED pathdrift，sizeUNKNOWN |
| README:87,112 |128tests |133collected116pass17fail | CONFIRMED T01 |
| READMEstructurepreprocess.py | src/ocr/preprocess.py | packagepreprocess/active；preprocessor.py另一套orphan | CONFIRMED X02 |
| READMEstructureartifacts/spec | spec存在 | 沒trackedfile | CONFIRMED X01 |
| README授權 | 僅個人離線 | 無model授權manifest，不能當所有dependency/modellicense | UNKNOWNcommercialrights，B04 |
| PACKAGINGNOTES:3–16 | 預設.spec/buildcommand | 不存在tracked spec，currentbuildcollect/exclude同時存在 | CONFIRMED B01/X01 |
| PACKAGINGNOTES:20–28 | hostpipinstall讓excludedEXE用Paddle | noexternalengineimport機制，toyonefileimportfailure | CONFIRMED B02 |
| PACKAGINGNOTES:33–56 | paddle.fluid/ppocrbundledata/舊hiddenimports | installed3.7PaddleX-modelcache/localdirs不同，providerconstructor也不同 | CONFIRMED staleAPI/assetpath，cleanWinUNKNOWN |
| PACKAGINGNOTES:60–68 | _extract_from_2x/_extract_from_3x support | methods不存在；mapper只attribute，realmappingemptydone | CONFIRMED O08 |
| PACKAGINGNOTES:72–73 | 2.6≤paddleocr<4都支援、Paddle≥2.5 | actualpredict3.xAPI，2.xconstructor/predict不保證 | CONFIRMED unsupportedcontract |
| PACKAGINGNOTESsize1.7–2.2GB | expectedFullEXE | 無HEADclean-machinebuildprofiler，不能當測量 | UNKNOWN |
| SMOKEheader |v1.0.8Patch1–7 | currentAPP_VERSION1.6.1 | CONFIRMED drift |
| SMOKE:11 |python main.py | nonexistententry | CONFIRMED |
| SMOKE4.1 |快速3熱鍵→3captureoverlay | singleoverlaycallback可被覆；queuehandoff已重現 | INFERREDfailure頻率，不能當已驗收 |
| SMOKE6.2 | blank/blacksoftfailstatusfailed | _process_results([])doneconfidence0；native4K空done | CONFIRMED |
| SMOKE7.2 |thumbremain | runtime和probe符合 | CONFIRMED match，不能all文件皆錯 |
| SMOKEH-B.2 |fallback [paddleocr_v5_mobile] | registry/namepaddleocr_v5 | CONFIRMED |
| SMOKEH-B.3 |second文字應優於或等於primary | rawconfidencecomparison不證GTbetter，adapter丟native | CONFIRMED noacceptanceevidence |
| SMOKEinstallgreendiag |pipinstall→已可用 | import≠models/nativeinputschema可用；EXEhostpip問題 | CONFIRMED |
| SMOKE底部 |114passed |116pass17fail/133collected | CONFIRMED |
| REVIEWER:14,119 |128pass/noerror |本次17fail | CONFIRMED |
| REVIEWERdefaultfalse/mobile | false/paddleocr_v5_mobile | actualtrue/v5、startup新keyforceenabled | CONFIRMED |
| REVIEWERtestdistribution |9files125/128舊分布 | current13testfiles，新build/version/widget/Ocrprogress等未列 | CONFIRMED |
| REVIEWERbuildsection |cdartifacts/spec150–200MB/cleanWincheck | scriptbuildactual，cleanWin未執行 | CONFIRMED path；UNKNOWN gate |
| REVIEWERH4style |module-levelguard存在 | currenttryfromPaddleOCRImportError存在 | CONFIRMED match，但guard不證modelready |
| REVIEWERcompletepaths |OCRreports存在算交付 |兩report空template無GT，fixturefolder不存在 | CONFIRMED存在但非accuracy驗收 |
| OCRCOMPARISONREPORT A/B/C | A baseline preprocess off、Bpreprocesson、Csecondary | activefirstpass固定raw，legacycfg不能switch完整pipeline；需實驗明確namedparams | CONFIRMED wiringdifference |
| OCRSAMPLETESTLOG fixturepath |tests/fixtures/ocr_samples |HEAD無path；此audit新dataset另保存 | CONFIRMED |
| providers/__init__docstring |knownpaddleocr_v5_mobile | realmapv5/cnocr_traditional_chinese | CONFIRMED |
| downloadscriptdoc |models/paddleocr/det/rec/cls與clsdoc_ori | actualfolderMODEL_MAPname、textlineori；defaultOCRgeneration未固定 | CONFIRMED |
| downloadscriptcomplete |複製後完全離線 | runtime不指定copydir、build不加assets | CONFIRMED B03 |
| DbWorker classdoc |所有DBwrite在其thread | widget/editor/tagdirectwrite＋actualtrace | CONFIRMED C03 |
| apponocrcomment |先更新DB確保持久化後刪圖 |只是queueupdate，立即刪source | CONFIRMED D02 |
| Configbackcompatcomment |缺secondary_engine時舊key推斷 |merge先補newdefault，Nonebranch只explicitnull | CONFIRMED F03 |

## Correction order / documentation gate

先寫清現行固定HEAD可執行入口與能力，移除無實證accuracy/size/完全offline承諾；把blankreports標模板、測試結果標具體commit/env。等待canonicalconfig/provider/persist/fusioncontract修復後同步UI、README、smoke、tests；不要在文檔先描述未實作的模型/文字庫。

命令gate是freshcheckout實跑sourceentry/installation、builddriver但cleanWindowsonly可驗Windowsartifact，pathchecker不能代替runtime。No產品code/doc已修改；此文件記錄drift與修正依赖。每row的fullimpact、rootfix與regression在相關masterissue。
