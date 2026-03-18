@echo off
cd /d "%~dp0.."
echo ============================================================
echo  下載 PaddleOCR PP-OCRv5 繁體中文模型到專案資料夾
echo ============================================================
python scripts\download_paddleocr_models.py
pause
