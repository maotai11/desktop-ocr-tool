# Test Integrity / Real Collection and Execution

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## Actual test execution

原corerequirements＋dev，Qt offscreen：collection133，passed116，failed17，errors0，skipped0，xfail0，xpass0；14.67s為pytestwalltime。完整testnames/stacktraces在pytest-run.txt與pytest.xml。pytestsummary沒有warningsection；這只表示該run未報pytestwarnings，不將UI/SDKstderr的nativewarnings誤合成0全環境warning。optionalPaddle/modern安裝後的nativebench不是這次pytest環境，freeze分开。

| Source | Claim | Actual currentHEAD |
|---|---|---|
| README | 128 tests /128structurelabel | 133collected，17failed |
| REVIEWER |128passed /secondenginefalse＋paddleocr_v5_mobile | actualdefaultsTrue/paddleocr_v5；subset分布漏新testfiles |
| SMOKE |114passed /v1.0.8 | currentv1.6.1、actual116pass17fail |
| OCR reports | A/Bmetrics欄位 | 未填模板，沒有nativeGT驗收 |

## Collected per file（JUnit classname）

| File group | Count |
|---|---:|
| test_build_script | 1 |
| test_capture_worker_drain | 2 |
| test_db_worker | 7 |
| test_h2_providers | 22 |
| test_h3_config_wiring | 26 |
| test_h4_fallback_edge_cases | 14 |
| test_main_window_batch | 11 |
| test_ocr_preprocess | 16 |
| test_ocr_worker_progress | 1 |
| test_repository | 11 |
| test_secondary_engine | 19 |
| test_version_constants | 1 |
| test_widget_interactions | 2 |

## Failed tests taxonomy

| Failure group | Cases | Root / do not simply changeassert |
|---|---:|---|
| H2factory/kwargs/registry/providername | 4 | 仍以paddleocr_v5_mobile作known/name，registry已改v5；可能需migration而不是宣稱enginebug |
| H2old2.xmapping 4 cases | 4 | 當前mapper沒有2.xnestedlist支援；docs仍說有，功能contract需決定 |
| H2integrationprovidercalled | 1 | unknownoldfactoryprovider變Null，mockrouting與runtime不一致 |
| H3defaults/getdefaults | 3 | 預設false/mobile與actualtrue/v5衝突；不以「測試通過的default」代替source |
| H3get_secondary_info | 1 | namecontract漂移 |
| H4_paddleocr_pkgpatch/load/idempotent/ensureloadedfailure | 3 | 現模組只有PaddleOCRsymbol，patch不存在舊pkgattribute即fail |
| H4engine.ocr exceptionmock | 1 | actualpredict，不調mocked.ocr，所以返回emptydone而非failed；這個mock不能擋真PIL/schema問題 |

總計17。各fail見原log；不是17個獨立高severitybug。應先定义支援版本/名稱/default/真nativeinput/output，再repairtestcontract。故障修完tests才合理更新；刪測試或改expected空done會掩O08。

## Meaningful coverage / limitations

Repository CRUD/FTSparams、DbWorkerlogicalslots、MainWindowmultiselectcontext、widgetdrag/search/version/buildcmd部分有實際可失敗assertion；不能一律說全部slop。capture tests預先排3task並直接run、patchisRunningFalse，不經QThreadhand-off，因此其pass不防C01。OcrEngine大量_engineMock回傳假box，不量det/rec或modeldict。H4fakeattributeOCRResult物件與nativeMapping不同，可全部通過仍丟真結果。test_h3主要cfgget/mockconfigure，不包含startup的新secondary_enginebranch，不能阻F03。Buildtestmocksubprocess並驗命令字串，不是packagedappsmoke。

UI常用stubtagrepo或不傳，漏掉正式SettingsDialogNameError。currentexportdefaultbranchpass不能阻allbranchattribute錯。文件「最多128tests」不是coverage或integration數據。本審計新增的是外置diagnostic harness，沒有以新增testcount替代fix證據。

| Missing gate | Concrete regression it would catch |
|---|---|
| actual appsettings withrealTagRepository | F01 NameError |
| nativeprovider ndarray/PIL/resultMapping | O08 emptydone |
| canonical configlive/restart/legacy | F03與UIprovideralias |
| realQThreadboundary/shutdown/processabort | C01/C02 |
| SQLitecontrolledtransactionfault＋persist/fileack | C03/D02 |
| dedup exactimage versuspHash | D06/D07漏圖/孤兒 |
| whitespace/spatialGT | O01/O02 |
| nativeTaiwanOCR GTcroprecognition/poly detection | vocab與resize／CERrootcases |
| 100/500UIlifetime＋RSS | P03/P04 |
| cleanWindowsfrozen/noPython/noInternet | B01/B02/B03及DLLclosure |
| dependencytelemetrycoldnativegate | S02 |
| clipboardcanary/retention/delete failure | S01 |

CI：tracked沒有.github/workflows或其他CIconfiguration；CONFIRMEDgap。Windowsclean-machine、realmultiDPI／accessibility、stress/profiler不能由Linuxoffscreenunit替代。新suite應分純unit、nativeofflinecontract、GT/profiler、Windowsplatform/package；model下載只在可驗hash的preparestage，executionstage拒網路。確認測試能fail有意義：對productionbug做mutation，對照gatefail而非只assertmockcalled。

No修復宣稱：目前master仍17fail。沒有把任何來源資料刪改來達綠燈。只有benchmark/報告/diagnostics檔案。
