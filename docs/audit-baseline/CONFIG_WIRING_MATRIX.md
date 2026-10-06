# Config Wiring Matrix

審計對象：`maotai11/desktop-ocr-tool`，master 固定為 `f5af545cabc4e9a649cd57497fbf5bf42f2ff138`（v1.6.1）。檢查日期：2026-10-02；原始程序時鐘與 UTC 紀錄見 evidence。產品 source 未修改。

證據標記：**CONFIRMED**＝可定位的程式／實測；**INFERRED**＝原因或實際影響仍缺現場證據；**UNKNOWN**＝本環境無法驗證。程式存在不等於功能接線，合成測試不等於現場準確率。每項 issue 的完整影響、修正驗證與 regression 在 [MASTER_FINDINGS.md](MASTER_FINDINGS.md)。

環境為 Linux x86_64、Python 3.12、Qt offscreen；不是 Windows clean machine。原始 core requirements 的測試環境及後續 optional benchmark 環境分別記錄於 `evidence/pip-freeze.txt`、`evidence/benchmark-pip-freeze.txt`。後者 cv2 已被 Paddle 相依 wheel 改寫，不能跨環境比較 latency。所有完成的 OCR 量測均先設定 ORT opt-out 並使用 syscall 網路阻斷。

## Authoritative config 與判準

Python `DEFAULT_SETTINGS` 是缺檔及merge的來源。`config/default_settings.json` 只打包為data，ConfigManager.load從未讀取；其中 max_image_short_side=1280，而Python=960。ConfigConsumer必須改變runtime動作，log、UI回填、set/save不計consumer。已存settings可覆蓋defaults；舊key存在不自動代表仍有用途。F01使正式app設定頁目前無法打開，下表UI為宣告controls與no-tag診斷路徑，不冒充正式可用。

| Key | 真正 default | UI control | Runtime consumer / life | Test presence與限制 | Packaging | 判定 |
|---|---|---|---|---|---|---|
| `general.language` | `"zh-TW"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；UI文字固定zh |
| `general.start_with_windows` | `false` | _cb_autostart | src/app.py:339; src/ui/settings_dialog.py:523 set_autostart（frozen即時） | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live(frozen)/STARTUP；nativeWin未測 |
| `general.start_minimized` | `true` | _cb_start_min | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；widget.show unconditional |
| `general.single_instance` | `true` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD KEY；mutex固定always |
| `general.data_directory` | `"./data"` | 無 | src/core/config.py:150 get_data_directory → app.py:52 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE STARTUP |
| `capture.backend` | `"mss"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；factory固定Mss |
| `capture.screenshot_format` | `"png"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；worker固定PNG |
| `capture.jpg_quality` | `95` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；非設定consumer |
| `capture.include_cursor` | `false` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；沒有設定consumer |
| `capture.auto_ocr_on_capture` | `true` | _cb_auto_ocr | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；region_ocralways/fullscreennever |
| `capture.capture_sound` | `false` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；沒有設定consumer |
| `capture.save_raw_image` | `true` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；region_ocr無條件cleanup |
| `ocr.engine` | `"onnxruntime"` | 無 | 無 active consumer | tests/test_h4_fallback_edge_cases.py, tests/test_h3_config_wiring.py, tests/test_ocr_preprocess.py, tests/test_ocr_worker_progress.py, tests/test_secondary_engine.py, tests/test_h2_providers.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；固定RapidOCR |
| `ocr.model_det` | `"models/det/pp-ocrv4_det.onnx"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD Runtime；get_model_path未call |
| `ocr.model_rec` | `"models/rec/pp-ocrv4_rec.onnx"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD Runtime；get_model_path未call |
| `ocr.model_cls` | `"models/cls/pp-ocrv4_cls.onnx"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD Runtime；get_model_path未call |
| `ocr.language` | `"chinese_cht"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；packageDefault＋固定zh-hant |
| `ocr.confidence_accept` | `0.85` | 無 | src/app.py:106 | tests/test_h4_fallback_edge_cases.py, tests/test_ocr_preprocess.py, tests/test_h2_providers.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE STARTUP |
| `ocr.confidence_review` | `0.6` | 無 | src/app.py:107 | tests/test_ocr_preprocess.py, tests/test_secondary_engine.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE STARTUP |
| `ocr.enable_second_pass` | `true` | _cb_second_pass | src/app.py:109 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `ocr.enable_handwriting_mode` | `false` | _cb_handwriting | src/app.py:110 | tests/test_h3_config_wiring.py, tests/test_secondary_engine.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `ocr.max_image_short_side` | `960` | _sp_short_side | src/app.py:108 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `ocr.primary_engine` | `"rapidocr"` | _primary_engine | src/app.py:91 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | LOG-ONLY；UI選擇不換主引擎 |
| `ocr.secondary_engine` | `"paddleocr_v5"` | _secondary_engine_combo | src/app.py:93 | tests/test_h3_config_wiring.py, tests/test_secondary_engine.py, tests/test_h2_providers.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | CONFLICT；startup用這個，不看enable |
| `ocr.auto_switch_secondary` | `true` | _auto_switch | src/app.py:101 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | LOG-ONLY；不能禁用actualfallback |
| `ocr.auto_switch_threshold` | `0.75` | _auto_switch_threshold | src/app.py:102 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | LOG-ONLY；actual另用secondarythreshold |
| `ocr.enable_secondary_engine` | `true` | _sec_enabled | src/app.py:96 | tests/test_h3_config_wiring.py, tests/test_secondary_engine.py, tests/test_h2_providers.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | PARTIAL/CONFLICT；live用，restart可能forceenabled |
| `ocr.secondary_engine_provider` | `"paddleocr_v5"` | _sec_provider | src/app.py:97 | tests/test_h3_config_wiring.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | PARTIAL/CONFLICT；live用，startup通常被secondary取代 |
| `ocr.secondary_engine_for_handwriting` | `true` | _sec_handwriting | src/app.py:126 | tests/test_h3_config_wiring.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `ocr.secondary_engine_for_low_confidence` | `true` | _sec_low_conf | src/app.py:127 | tests/test_h3_config_wiring.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `ocr.secondary_engine_confidence_threshold` | `0.85` | _sec_threshold | src/app.py:121 | tests/test_h3_config_wiring.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `preprocessing.enable_deskew` | `true` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ORPHAN CONSUMER；只有未import舊preprocessor讀取 |
| `preprocessing.enable_denoise` | `true` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ORPHAN CONSUMER；只有未import舊preprocessor讀取 |
| `preprocessing.enable_contrast_enhance` | `true` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ORPHAN CONSUMER；只有未import舊preprocessor讀取 |
| `preprocessing.enable_shadow_removal` | `false` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ORPHAN CONSUMER；只有未import舊preprocessor讀取 |
| `preprocessing.binarize_method` | `"sauvola"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ORPHAN CONSUMER；只有未import舊preprocessor讀取 |
| `clipboard.monitor_clipboard` | `true` | _cb_monitor | src/app.py:164 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | STARTUP-ONLY；save未stop/start watcher |
| `clipboard.auto_save_text` | `true` | _cb_auto_text | src/app.py:169 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | STARTUP-ONLY；save未disconnect callback |
| `clipboard.auto_save_image` | `false` | _cb_auto_image | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD/UI-ONLY；image_captured無handler |
| `clipboard.auto_ocr_on_clipboard_image` | `false` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；無image→OCRroute |
| `clipboard.ignore_self` | `true` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD KEY；customMIME ignore固定always |
| `clipboard.max_text_length` | `50000` | 無 | src/app.py:175 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `clipboard.deduplicate` | `true` | 無 | src/app.py:81 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE STARTUP |
| `history.max_items` | `10000` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；10500rows超10000 |
| `history.auto_archive_days` | `90` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；无scheduler |
| `history.auto_delete_archived_days` | `0` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；无scheduler |
| `hotkeys.capture_region_ocr` | `"Ctrl+Shift+O"` | _hk_edits（read-only） | src/app.py:192-237 hk dict→register(action) | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | STARTUP；需重啟，UI不能編 |
| `hotkeys.capture_region_image` | `"Ctrl+Shift+S"` | _hk_edits（read-only） | src/app.py:192-237 hk dict→register(action) | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | STARTUP；需重啟，UI不能編 |
| `hotkeys.capture_fullscreen` | `"Ctrl+Shift+F"` | _hk_edits（read-only） | src/app.py:192-237 hk dict→register(action) | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | STARTUP；需重啟，UI不能編 |
| `hotkeys.toggle_widget` | `"Ctrl+Shift+Space"` | _hk_edits（read-only） | src/app.py:192-237 hk dict→register(action) | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | STARTUP；需重啟，UI不能編 |
| `hotkeys.paste_last` | `"Ctrl+Shift+V"` | _hk_edits（read-only） | src/app.py:192-237 hk dict→register(action) | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | STARTUP；需重啟，UI不能編 |
| `hotkeys.open_console` | `"Ctrl+Shift+M"` | _hk_edits（read-only） | src/app.py:192-237 hk dict→register(action) | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | STARTUP；需重啟，UI不能編 |
| `hotkeys.quick_search` | `"Ctrl+Shift+Q"` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；action/registration不存在 |
| `ui.theme` | `"system"` | _cb_theme | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；staticdarktokens，restart也無consumer |
| `ui.widget_opacity` | `0.95` | 無 | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；無setWindowOpacity consumer |
| `ui.widget_position` | `"remember"` | 無 | src/ui/widget.py:347; src/ui/widget.py:372 | tests/test_widget_interactions.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `ui.widget_max_items` | `20` | 無 | src/ui/widget.py:596 | tests/test_widget_interactions.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `ui.thumbnail_size` | `[80, 80]` | 無 | src/app.py:76 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE STARTUP |
| `ui.font_size` | `13` | _sp_font_size | 無 active consumer | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | DEAD；各widget硬編px，restart無consumer |
| `ui.widget_click_action` | `"select"` | 無 | src/ui/widget.py:532; src/ui/widget.py:542 | tests/test_widget_interactions.py；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE live/configread |
| `ui.font_family_priority` | `["Microsoft JhengHei UI", "Microsoft JhengHei", ""]` | 無 | src/app.py:47 | 無key/identifier引用（不等於無behavior unit）；非全程驗證 | Python default 在code；JSON未消費；Win gate UNKNOWN | ACTIVE STARTUP |

## Dynamic keys / 実時差異

`ui.widget_saved_x/y` 在 FloatingWidget close/move記錄與下一次restore消费，沒有settings control；也不是deadkey。history UI手動archive/delete是真的repo/DbWorker操作，但不能替代自動retention job。一般capture存PNG不由formatkey控制。

OCR短邊/second-pass/handwriting在 `_save→configure` 即時改；confidence_accept/review僅啟動讀、無settingscontrol。備援 live 用 enable_secondary_engine/provider，restart用 secondary_engine且forceTrue；舊檔缺新key先merge成paddleocr_v5，舊相容branch只有明確null可達。不要將其全說成不可達或無用途。

preprocessing.enable_denoise/contrast false 不會關active second-pass的固定CLAHE/denoise；deskew、shadow/Sauvola不在activepipeline。`max_image_short_side` 實際是最低短邊，不是最大像素，短邊大於1920另固定downscale；UI可設2048也不修改1920硬cap。

測試key引用只表示測試出現字串。test_h3主要get/default/mockconfigure，不構成 UI→runtime→restart→package 全鏈。可重複gate：逐key改兩個值，記action/provider/model identity/DB/files/Qtvisibility，再live与restart對照；package以相同manifest與cleanWin重跑。
