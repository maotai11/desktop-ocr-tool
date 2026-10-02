# Security / Privacy / Microsoft Connection Forensics

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## Desktop threat model

資產：剪貼簿全文、辨識截圖/縮圖、人工校正/備注、DB與WAL/備份、匯出內容、API/token/密碼、公司/人名PII、model/dependency可執行nativecode。信任邊界是來源app→Clipboard/OCR→本程序→portabledatafiles→匯出消費app、模型/套件下載→nativeinference，以及不同帳戶/共享目錄/backup。不是網頁server；沒有證據顯示暴露網路API，所以不製造遠端SQLinj/RCE結論。

| Actor / access | Concrete ability / evidence | Risk boundary |
|---|---|---|
| 正常使用者誤copy敏感內容 | defaultmonitor+autosavetext收錄，fake1000/1000saved | token/PII長期保存；是否真用戶曾收錄UNKNOWN |
| 同帳戶其他程式/backupsync | plaintextDB/thumb/export可讀，檔案未加app層加密 | 同user compromise本已可讀；不能當跨userremotecritical |
| 共享portable資料目錄另一user | 是否可讀由WindowsACL/inheritedpermissions決定 | 未取得nativeACL，UNKNOWN |
| 控制外部copytext的app | canaryformula/rawmarkup入CSV/QLabel | spreadsheetinterpretation与previewspoof，無RCE實證 |
| 可改本地DB/檔案者 | traversalpath導致delete/ZIPread，模型runtimehash沒驗 | 輸入path越界範圍可重現；precondition必說明 |
| dependency/modelpublisher或遭供應鏈替換 | 任意nativewheels/ONNX、未locked>=、DLLclosure | 未發現遭入侵package，closure與hash仍gap |
| 外部telemetry/modeldownload服務 | ORT1DS、optionalPaddle/CnOCRmodelhost | 不應以「離線OCR」推定無第三方connection |

## Collection / persistence / retention

CONFIRMED：monitor_clipboard=true與auto_save_text=true在startup接Watcher→text_captured→save；maximum50000characters不是sensitivefilter。只忽略自寫customMIME與相同來源最近60秒hash，不看密碼視窗、token格式、sourceapp權限或retention。schema的source_app_name/window_title没有producer，不能聲稱有來源排除。用户可用traypause實時停Watcher，Settingscheckboxsave則只改config，下次restart作用，而且正式settingspage目前F01打不開。

History.max_items=10000、archive90天/delete0天沒有scheduler；10500rows、老日期0archive實測。檔案/SQLite/FTS/WAL/exports分開assets，刪raw後thumb仍存在，rowsoftdelete僅遮查詢；harddelete不是backup/exports已刪除證據。SQLite此環境secure_deletepragma見dedup.json；即使ON也不能保證FTS頁、WAL、先前backup、SSD已擦除。敏感資料保存已CONFIRMED機制，未接触真usersecret。

## Microsoft 連線的根因與處理

1. 本次按 `onnxruntime>=1.20` resolve到官方1.30.0。官方POSIXruntime可預設啟用1DStelemetry；nativebinary含OneCollector endpoint。
2. `PosixTelemetry::Initialize`在首次ORT環境建立時檢查 `ORT_DISABLE_TELEMETRY=1`；沒有此flag會setupuploader/identitycache，初始化ProcessInfo不受後來API disable完全阻止。repo無flag或disable API。
3. 早期baseline/modern/500命令被自動審核以Microsoftpayload未知拒絕，沒有將被拒action當成功測量或當已capturedpacket。原因歸屬有source/binary/optout對照；**先前實際wirepayload、送達、OCRtext/screenshot內容一律UNKNOWN**。
4. 本次審計的隔離處理：**processlaunch前**flag，PythonAPI補充；audit-onlyLD_PRELOAD記socket/connect/DNS，Linuxseccomp擋native socket/connect/send與io_uring/x32路徑。guard_validation直接native syscall返回EPERM，localhostDNS也拒絕。隨後realRapidOCRinitialize與inferexit0，ORT專用probe沒有networkblocklog。
5. ptrace/strace被執行環境拒絕，因此沒有封包trace／解密payload，不冒充trafficcapture。Linuxscope不能外推WindowsETW。Paddlebenchmarklog有AF_INET6socketcreation被guard拒絕，只有socket建立，無endpoint/connectpayload；可能capabilityprobe，不能把它稱外洩。

官方source可觀察到潛在ProcessInfo欄位：runtimeVersion、OS描述、architecture、cpuModel、deviceClass、processorCount、totalMemoryMB、deviceid狀態；session事件有modelFileName/GraphName/producer/metadata、modelhash/providers/hardwaretypes。這些是**source-defined fields**而非本次封包內容；不能根據沒有text欄位就宣稱絕不含PII（modelfilename/metadata可能由使用者控制），也不能宣稱已上傳圖片。

可重複命令（限定Linuxx86_64audit）：

```bash
cc -shared -fPIC audit/benchmarks/network_guard.c -o network_guard.so
ORT_DISABLE_TELEMETRY=1 LD_PRELOAD="$PWD/network_guard.so"   audit-venv/bin/python audit/benchmarks/no_network.py   audit/benchmarks/telemetry_probe.py
```

這只修復**本次測試的執行條件**，沒有修改repository，沒有聲稱已完成產品privacyfix。產品後續需在所有ORTimport前設定/固定telemetry-freebuild/驗證每個平台的consent/ETW/網路行為；pinwheelhash並coldstartnetworkgate。離線modeldownload佈署與telemetry必須分開：合法fetch公開模型不能授權傳收集的userdata。

Primary source：[ORT1.30 Privacy](https://github.com/microsoft/onnxruntime/blob/v1.30.0/docs/Privacy.md)、[POSIXtelemetry source](https://github.com/microsoft/onnxruntime/blob/v1.30.0/onnxruntime/core/platform/posix/telemetry.cc)、[ORTreleases](https://github.com/microsoft/onnxruntime/releases)。字句／欄位以pinnedsourcehash原始證據檔核對。

## Security / privacy inspection matrix

| Surface | Verdict | Evidence / root / required test |
|---|---|---|
| Clipboardpassword/token/APIkey/PII | CONFIRMED機制／actualincidentUNKNOWN | defaultplaintextlongterm，fakecanary不使用真secret；S01 |
| RawPNG / thumbnail | CONFIRMEDplaintext | D02/D06cleanup/retention，thumb可見；刪row不保證外部backup |
| SQLite/WAL | CONFIRMEDplaintextSQLiteheader | SQLparams與FTStriggers存在；WALgrowth穩定不是encryption |
| Logs | CONFIRMED DEBUGroot＋30daysrotation | ownclipboardlog長度非全文；errors/providerupstreamstack可能包含paths，實payloadUNKNOWN |
| ExportTXT/CSV/JSON/ZIP | CONFIRMEDplaintext | CSVcanaryrawformula；ZIPacceptoutsidepath、flattenbasenamecollision；无import解壓故不是ZipSlip |
| Temporary / onefile extraction | INFERRED/UNKNOWN | _MEIPASSassets解壓；實WintempACL/残留model/behavior未測；無secretOCRtemp公開網路證據 |
| WindowsACL/data dir | UNKNOWN | repo沒有explicitACLchmod／AccessControl，inherits現場權限；portableexe旁可移共享folder |
| Autostartregistry | CONFIRMEDcode，nativeUNKNOWN | HKCU Run、quotedexe path；devmode跳過，不寫HKLM；外部修改config為false不會在startup自動移除已存在entry |
| Globalhotkey | CONFIRMEDsixregistrationcode | 衝突onlylog、nativeWin未知；不是keylogger，沒有allkeycapture code |
| CSVinjection | CONFIRMEDrawformula，executionUNKNOWN | S03，Excelimport及locale要nativegate |
| Pathtraversal | CONFIRMEDlocalDBprecondition | S04tempsentinel删除，WinUNC/symlink未知；同useraccess限制severity |
| SQLinjection | 未在檢查範圍確認 | boundparameters、queryconditionshardcoded；x'ORcanary无dataescape，不當全面證明 |
| FTSquery | CONFIRMEDquoting與syntaxfallback | %/_LIKEwildcards扩查、CJKFTSless5走LIKE；quote空syntaxsafe失败回退，无任意SQLexecution |
| Richtext | CONFIRMEDAutoTextpreview | OCReditorPlainText、noteHTML；externalresource/QtRichTextfetch行為UNKNOWN，S05不叫browserXSS |
| Supplychain / nativeDLL | CONFIRMEDunlockedresolvegap | B04；hashmodelonlybuildrepo非runtime；DLLsearchside-load需cleanWinProcMon，不虛報漏洞 |
| Modelintegrity/tamper | CONFIRMEDruntimevalidatororphan | O06，buildverify三個repo模型pass，但實載packagehash未mandatory檢查 |
| Optionalmodeldownload | CONFIRMEDconstructor不指定cachelocaldirs | 冷cacheofflineavailability UNKNOWN；repo下載script不接runtime B03 |
| Update機制 | 未找到activeupdater | 90trackedfiles現HEAD與dynamicimports已查；外部release渠道與manualupdates不作內建機制 |
| Gitsecrets | 限定pattern未發現 | 34commits194textblobs，specificAWS/GitHub/OpenAI/privatekeypatterns0candidate；不是allsecretsabsenceproof |
| PyInstallerbundle | UNKNOWNcleanWin | Linuxtoytestexcludedhostpip不能inject，fullDLL/module/modelclosure未驗 |

## Proposed privacy gates（尚未實作）

Fakecanarycollect/pause/disable/restart/expiration，驗DB、FTS、WAL、raw/thumb/export與backupscope；deletedataack包含filepermissionfail與crashrecovery。Network gate在native coldstartup、realOCR、secondarycoldload/settingopen/updatepaths，記sourcehost/payloadschema/consent，確認telemetrycache不存在；不將socketcreation等同傳資料。NativeWindows確認ACL/portable/temp/ETW/DLLsearch與HKCURun；spreadsheetcanary確認安全模式不破壞金額負號。沒有以上實證不能稱「完全離線所以安全」或「privacy已修好」。
