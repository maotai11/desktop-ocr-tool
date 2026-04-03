# Packaging Notes — DesktopOCRTool

## 預設 EXE（artifacts/DesktopOCRTool.spec）

`paddleocr` 及 PaddlePaddle 整個堆疊已列入 `.spec` 的 `excludes`，**預設 EXE 不包含第二引擎**：

```python
excludes=['paddle', 'paddleocr', 'paddlex', ...]
```

打包指令：

```bash
cd artifacts
pyinstaller DesktopOCRTool.spec
# 產出：dist/DesktopOCRTool.exe（約 150-200 MB）
```

## 啟用第二引擎的方式

第二引擎為 optional dependency，**不需要打進 EXE**。使用者在安裝環境執行時安裝即可：

```bash
pip install paddleocr
```

安裝後，在 Settings → OCR 分頁 → 「第二引擎」→ 勾選「啟用」即可生效。

## 若要將 paddleocr 打進 EXE（不建議）

> **警告**：PaddlePaddle + paddleocr 會增加約 1.5-2 GB，不適合「離線輕量」定位。

若確有需要（例如完全離線的封閉環境），修改 `.spec`：

1. 從 `excludes` 移除 `'paddle'` 和 `'paddleocr'`
2. 在 `hiddenimports` 補充：

   ```python
   hiddenimports += [
       'paddleocr',
       'paddleocr.paddleocr',
       'paddle',
       'paddle.nn',
       'paddle.fluid',
   ]
   ```

3. 在 `datas` 補充模型檔（需先執行一次 recognize() 讓 paddleocr 下載模型）：

   ```python
   import os, paddleocr
   paddle_model_dir = os.path.join(os.path.dirname(paddleocr.__file__), 'ppocr')
   datas += [(paddle_model_dir, 'paddleocr/ppocr')]
   ```

4. 重新打包，預期產出 EXE 體積約 1.7-2.2 GB。

## 版本相容性

| paddleocr 版本 | 結果格式 | 支援狀態 |
|---------------|---------|---------|
| 2.x (含 2.6.x) | list of pages | 支援（`_extract_from_2x`） |
| 3.x (PaddleX backend) | OCRResult 物件（dt_polys / rec_texts / rec_scores） | 支援（`_extract_from_3x`） |

`PaddleOCRv5Provider._map_result()` 自動偵測格式，使用者無需手動設定。

## 依存套件版本建議

```
paddleocr>=2.6,<4.0    # 2.x 或 3.x 均可
paddlepaddle>=2.5      # CPU 版本；GPU 版本換 paddlepaddle-gpu
```

在 CPU 環境安裝 CPU 版：

```bash
pip install paddlepaddle
pip install paddleocr
```
