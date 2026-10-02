# Orphan / Dead / Duplicate Architecture and Data Integrity

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## Runtime reachability inventory

Method：全部90trackedpaths，ASTimports/declarations/calls/config/dispatch（source-index.json），全文identifier搜尋，從src/main.py→app及Qtconnect追reachable。dynamicimport provider factory以字串registry人工加入；Qtmethodslot不是普通call所以不能以identifiercount0判dead。`rg --files`會遵循.gitignore中的data/，曾漏src/data；本審計改用gitls-files與直接AST全掃，所有7個trackeddata模組已檢查。刪碼候選與not-in-runtime不同；未自動刪任何檔。

| Artifact | Active status | Evidence / classification / impact |
|---|---|---|
| src/ocr/preprocessor.py::preprocess_image | ORPHAN pipeline | noactiveimport；有cfgpreprocessingreader、deskew/shadow/Sauvolafallback，未run_ocrcall |
| src/ocr/preprocess/__init__.py | ACTIVE | engineimport enhance_for_ocr/upscale_if_small；fixedsecondpipeline |
| preprocess/contrast.py / denoise.py / resize.py | ACTIVE helpers | package调用；resize同時有tests，非orphan |
| preprocess/deskew.py::deskew_image | Runtime ORPHAN | onlyorphanpreprocessorimport，外部deskewdependency不因此active |
| src/ocr/model_validator.py::validate_models | ORPHAN runtime validator | nocall/import；build自己的verify_models是真scriptcall，duplicate SHAhelper但目的different |
| src/core/signals.py AppSignals/get_signals | ORPHAN bus | 无import，app用各worker直接signals；signal宣告不等於dispatch |
| postprocessor.calculate_avg_confidence | unused helper | noactivecall，engine/provider手動平均重複；不能宣稱fusioncalibration |
| ConfigManager.get_model_path | noactivecaller | 路徑/frozen_MEIPASS處理存在但load()不叫 |
| models/det/rec/cls/*.onnx | SCRIPT-ONLY / runtime orphan copies | buildhashverify；apppackage自带模型，currenthash相同，不以filename判modelversion |
| models/models.lock.json | ACTIVE build-only | scriptsbuild.verify_models/generate_lock，不是runtimeintegrity/dependencylock |
| dictionaries/custom_tw_corrections.json | ORPHAN | noactivefilename/contentload；4pairs/11terms不是已生效詞庫 |
| scripts/download_paddleocr_models.py | separate tooling | 使用者手動呼叫才download；copylocalfolder無providerconsumer/add-data |
| secondary_engine.py SecondaryEngineBase/Null | ACTIVE abstraction | engine與dynamicproviderfactory使用，不能因abstractmethods无body判dead |
| providers/CnOCR | OPTIONAL reachable | factorycanonicalname，依賴沒裝availablefalse；UIcnocr alias未接 |
| src/data/* | ACTIVE | main/DB/UI/export/captureimport，ignorepatterns導致rgfiles漏不是dead |
| MssBackend.close/get_monitor_info | noactivecall候選 | close無sctbydesign；get_monitors也无overlayconsumer，Winmultimonitor功能未用 |
| CaptureOverlay.region_selected | DECLARED, noemit/connect | runtimecallback不是signal；不能從它推有取消/選區通知 |
| OcrWorker.engine_loading | EMIT, noappconsumer | progress/ready/failedactive，不能整worker信號都說dead |
| DTO.source_app_name/source_window_title | schema/default exists, noactiveproducer | capture/clipboard未讀foregroundapp，所有正常插入None |
| annotation_path/update_annotation | model/repo/exportpath存在、無activeannotationeditorproducer | dormantstorecapability不代表有畫圖功能 |
| Tags association | Repofunctions active-capable，UIapply未接 | settingspage功能虛稱F05；既有tagassociation外部使用UNKNOWN |
| docs/OCR_COMPARISON_REPORT/OCR_SAMPLE_TEST_LOG | EMPTY TEMPLATE | benchmarklabelsA/B/C未填，不是已有accuracydata；fixturespath不存在 |
| tests H2/H3/H4 oldprovider mocks | STALE, collectedfail | 不稱orphan(仍被pytest執行)，但與runtimecontract脱離 |
| artifacts/DesktopOCRTool.spec / src/ocr/preprocess.py | NONEXISTENT paths | docsorphans，actualscriptsbuild與packagefolder不同 |

## Duplicate / conflicting abstractions

兩套preprocess（只有packagehelperactive）、兩套secondaryconfig（live vsrestart），三個OCRmerge/avgconfidence implementations（engine/Paddle/CnOCR各自轉繁與平均），兩個model SHAvalidators（runtimeorphan vsbuildactive）、AppSignalsbus與workerdirectsignals。這些是已觀察並行實作，不全部一律要合併：先O02/O08/D01rootcontract，若helper融合不能消除schema／stageidentity錯誤，新增pipelineabstraction只增加complexity。

Backwardcompatibility：secondary_engine missing被ConfigManager.merge補default後appNone分支不走，但explicitnull仍走；不可標wholebranchunreachable。Paddle3.x註解「也支援舊格式」實際只檢查attributes，沒有list2.xmapper；_extract_from_2x/_extract_from_3x不存在。MODEL_MAP v5server與上游default世代不同需loadedmanifest，不能把script叫modelorphan就安全刪。

## Data integrity producer/consumer map

| Field / invariant | Producer → consumer | Verified consequence |
|---|---|---|
| raw_image_path | CaptureWorker/FileManager→DB→OCR/appcleanup→editor/ZIP | 原圖未等DBack刪；save_raw_image無consumer |
| thumbnail_path | create_thumbnail→DB→ItemCard/preview | raw清後保留thumb，mixed未必错误而capability不同 |
| item_type | insertimage/text→updateOCR判raw+text→clearpath不重算 | probes mixed＋rawNULL、thumb保留，rerundisabled |
| OCRstatus | captureinitialnone→OcrWorkerdone/review/failed；editorpending/confirmed | 初始regionOCR没有pending/processingupdate，workerexception没persistentfailedslot |
| engine/model | DTOdefaults→repoSQLite | adoptedsecondary仍baseline，D01 |
| detail_json / boxes | appresizedspace→DTOjson→DB | 無coordinate-space/inverse-transformfield；若用原圖疊框可能錯位，現UI未實作疊框 |
| content_hash | strippedtextSHA256→dedup/DB | whitespace-edge等價，非byteexact，也不是原圖contenthash |
| image_hash | DCT64bitpHash（scipy若缺換mean fallback）→dedup | 不同金額0–7/9可同碼，D07；samehash不保證sameimage |
| edited_text | editorwrite→effective_text | 空串truthiness回raw；rerun無條件NULL會覆edit |
| soft/harddelete | repo/DbWorker→filecleanup | soft只flag；hardrow先commit後filedeletefail，untracked剩檔 |
| FTS | insert/update/delete triggers→queryquotedMATCH＋LIKEfallback | queryvisibility1/0/0實測；不是secureerasure證明 |
| sourceapp/window/annotation | defaultNone, noactiveproducer | 不把schema存在當功能 |

去重先capture存檔再DbWorkerreturn，會留下無row PNG/thumb；dedup_probe confirms2captures=4files、1row。近60s pHash小改字碰撞可在detection之前丟圖，所以accuracyerror必先排除upstreamcollectionmiss。

Removal/fixgate：每candidate明確Runtime/Script/Test/Packaging四個entry，檢查字串dynamicimports/Qtcallbacks、舊config外部extension使用；先接identity/precision/ownership與migration，再選刪除或保留。沒有大規模重構。
