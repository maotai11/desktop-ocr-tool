@echo off
cd /d "%~dp0.."
echo 產生 models.lock.json ...
python scripts\generate_lock.py
echo 完成
pause
