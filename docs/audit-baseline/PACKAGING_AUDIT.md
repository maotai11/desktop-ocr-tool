# Packaging / Dependency / Deployment Architecture Audit

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## True build path / assets

CONFIRMED：只有 `scripts/build.py` 是trackedbuilddriver。它檢Python>=3.11、verify_models(repo/models.lock.json)（缺lock只warning跳過）、生成artifactsdirs並清舊輸出、PyInstaller --clean --onefile --windowed --name DesktopOCRTool --paths repo src/main.py、copy exe到versionedrelease、產生README.txt與ZIP。實際version來src/core/version.py，releasebundle/pathfunctions有tests。`artifacts/DesktopOCRTool.spec`不在HEAD，README不能當buildproof。未在repo執行destructiveoutputreset或替使用者發佈。

| Payload | Actual build handling | Runtime consumer / gap |
|---|---|---|
| Python/Qt | PyInstaller＋explicitQtCore/Gui/Widgets hiddenimport | Winplatform plugins/cert/runtimeDLL需cleanhost驗 |
| numpy/cv2/PIL | collect-allnumpy/cv2＋PILhiddenimports | cv2多wheelnamespace；nativesupplychain閉包未win驗 |
| rapidocr_onnxruntime | collect-data＋hiddenimport | package模型會進_MEIPASS，真正loadpackage defaults |
| repo models/* | prebuildhashverify但無--add-data models | validator檢的copy不是explicitruntimefile；currenthash相同恰巧匹配 |
| config/default_settings.json | add-data config | ConfigManager不讀，settings寫EXE旁config |
| dictionarycustom_tw | 沒add-data、無runtimeconsumer | 不是打包文字庫 |
| zhconv | collect-all | active_s2t，absenceidentityfallback；資料dict需bundle |
| deskew | requirements但activepipeline沒用 | 不能因wheel被收就說已去斜 |
| Paddle/paddlex/paddleocr | collect-data/binaries/hiddenimports同時exclude | contradictoryclosure；具體archive內容與sizeUNKNOWN |
| CnOCR/cnstd/torch | excludes整套 | 跟UIoptional名稱不一致；hostpip不能補到frozen |
| optionalmodels repo/models/paddleocr | noadd-data/noexplicitlocaldir | scriptcopy不是部署成功 |
| tests/IPython/tkinter等 | excludes | unit存在不算packagedsmoke |

## Frozen config/data/log/import paths

`get_project_root` frozen＝dirname(sys.executable)，config/settings.json、logs/app.log與相對data/寫exe旁；`_MEIPASS`是runtimepackage解壓assets來源。get_model_path可指_MEIPASS但無activecall。logger在src/main.py及main最早setup，檔案permissionfailure可在可寫性UI檢查之前出現；Winprotectedfolder完整UXUNKNOWN。portablemove會改相對data；absoluteconfigpath則固定。WindowsACL繼承、onefiletemp殘留、DLLsearch和EXEauthenticode沒有實測。

## Excluded onefile cannot adopt host pip automatically

CONFIRMED機制：PyInstallerLinuxtoyonefile使用excludedzhconv；hostvenv已裝zhconv，但frozenprobe sys.path只解壓bundle位置，import失敗。Repo没有loader/subprocessengine/PYTHONPATHsitepackageintegration。因此文件「pipinstallpaddleocr即可啟用既有EXE」不能成立於目前架構。不是測試actualWindowsDesktopOCRTool；actualfrozenimport還需cleanWindows。

已建toytest的fullbuildlog與executionJSON在frozen-build.log/frozen-probe.json；不能轉寫「Windowspackaging完成」。 sourceexecution在同一Pythonenvpip安裝則可能import，但仍PIL/mapping/defaultmodel/cache問題。

## Deployment architectures（分析，未自選）

| Option | Concrete runtime/dependency boundary | Required gate / tradeoff |
|---|---|---|
| Core build | 固定RapidOCR/ORT/字表/zhconv，移除互斥optionalcollect | v4GT/telemetryoff/hash、UI只顯core能力；不是保證小於200MB |
| Full build | 包Paddle/CnOCR與實際挑選generationmodels/nativeDLL | 大closure、CPU/RSS/load/授權、cleanNoPythonOffline；不能沿currentexclude同收 |
| Plugin build | versionedsubprocess或明確isolatedruntime，RPC傳本地data＋能力manifest | auth/integrity/compatibility/cancel/timeout/PII，不能任意hostsys.path注入 |
| Onedir | 同一embeddedruntime多檔，assets/nativeDLL可定位 | 更容易驗manifest/更新但檔案tamper/完整性與portablemove要測，仍不自動讀hostpip |
| Optional downloadable engine | appcontrolledprepare下載完整runtime/modelprofile，不只是model檔 | 對host/size/hash/授權/consent、atomicinstall、rollback、後續offline，清楚網路用途 |

決策輸入：可否聯網準備、機器CPU/RAM/磁碟、罕字/繁中/手寫GT需求、更新策略、商業modellicense、分發負擔與Windows維運。沒有bench不能選engine；沒有cleanmachine不能選定build已完成。

## Dependency modernization

| Axis | Legacy rapidocr-onnxruntime1.4.4 vs current rapidocr3.9.2 / optional stacks |
|---|---|
| Maintenance | PyPIlegacylatest2025-01-17，current2026-07-21；currentofficialrelease線持續v6，但legacy停止維護屬未有公告證據，不能單按日期斷言 |
| Accuracy / TC /rare | 此audit36GT＋cropmetrics，v6sample較低CER，v5scale敏感；不是官方accuracy搬來產品 |
| Memory /CPU/startup | controlledexperiment實測；defaultthread不同、v5preproc/default與nativePaddlepipeline不同，不混排名 |
| Package size | wheel與installedbytes由PyPI/metadata記錄；不代替PyInstallerEXEsize與onefileextractionpeak |
| PyInstaller compatibility | legacytoyimport＋actualcoretests；modernAPI/modelassets字表、PaddleDLL和CnOCRtorchclosure仍cleanWINGap |
| Offline | legacywheel自含模型可阻網推論；modernv6smallwheelincluded，v5先明確publicfetch/hash；optionallocaldirs必指定，telemetry另gate |
| License | distributionmetadata/projlicense需與每個model授權分開；repo无完整modellicense文件，commercialredistributionUNKNOWN |
| Migration risk | legacytuple→moderndataclass/TextRecInput；cfgdefaults移到v6且preprocessing不同，GT/det/rec/fusion/provenance tests不可省 |

Exactregistryidentities、uploadtimestamp/filehash/size、licenseexpression與installed footprint在dependency-registry.json。models.lock.json只鎖三個repoONNX，不鎖ORT/nativeDLL/optionalmodeldownloads。coreopencv-python＋headless、optionalcontrib供同cv2，安裝先後改变import版本。

## Clean Windows release gate（UNKNOWN / not run）

固定commit＋dependencyhashlock/profile＋runtime/modelmanifest；freshWin無Python、無pip、無cachedmodels、noInternet，build後包的每個可見UIengine choice實際invoke。核對FPload/modelsessionpath/hash/字表/locale與DBidentity，消除sharedmodel對偶無驗證。

單檔首次/再次/冷diskstartup、_MEIPASSextraction/cleanup、4K/extremecrop/second/fallbacklatency與RSS；DPI125/150/175/200、multi-monitor/negativecoords、hotkeyconflicts/tray/Windowslogout/RDP/sleep、portablepath/ACL/ProgramFilespermissions、dependencyDLLmodulepaths、update/rollback。未執行不得在報告填「passed」。
