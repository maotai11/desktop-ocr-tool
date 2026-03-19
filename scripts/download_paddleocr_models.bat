@echo off
pushd "%~dp0.."
echo ============================================================
echo  下載 PaddleOCR PP-OCRv5 繁體中文模型到專案資料娶
echo ============================================================
python scripts\download_paddleocr_models.py
popd
pause
