# -*- coding: utf-8 -*-
"""
打包腳本：使用 PyInstaller 打包為單一 EXE
執行：python scripts/build.py
"""
import sys
import os
import subprocess
import json
import hashlib


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    if sys.version_info < (3, 11):
        print(f"錯誤：需要 Python 3.11+，目前為 {sys.version}")
        sys.exit(1)
    print(f"Python {sys.version_info.major}.{sys.version_info.minor} OK")

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    # Validate models
    lock_path = os.path.join(root, 'models', 'models.lock.json')
    if os.path.exists(lock_path):
        with open(lock_path, encoding='utf-8') as f:
            lock = json.load(f)
        print("驗證模型 SHA256...")
        for key, info in lock.items():
            mp = os.path.join(root, info['path'])
            if not os.path.exists(mp):
                print(f"錯誤：模型不存在: {info['path']}")
                sys.exit(1)
            if info.get('sha256'):
                actual = sha256_file(mp)
                if actual != info['sha256']:
                    print(f"錯誤：模型 SHA256 不符: {info['path']}")
                    sys.exit(1)
                print(f"  OK: {info['path']}")
    else:
        print("警告：models.lock.json 不存在，跳過模型驗證")

    sep = os.pathsep
    dist_dir = os.path.join(root, 'artifacts', 'dist')
    build_dir = os.path.join(root, 'artifacts', 'build')
    os.makedirs(dist_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)

    paddle_dir = os.path.join(root, 'models', 'paddleocr')
    has_paddle_models = (
        os.path.isfile(os.path.join(paddle_dir, 'det', 'inference.yml')) and
        os.path.isfile(os.path.join(paddle_dir, 'rec', 'inference.yml')) and
        os.path.isfile(os.path.join(paddle_dir, 'cls', 'inference.yml'))
    )
    if has_paddle_models:
        print("PP-OCRv5 本機模型已就緒，一併打包進 EXE")
    else:
        print("警告：models/paddleocr/ 模型未就緒，EXE 將使用 RapidOCR fallback")
        print("        請先執行 scripts\\download_paddleocr_models.bat 再重新打包")

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--windowed',
        '--name', 'DesktopOCRTool',
        '--add-data', f'{os.path.join(root, "models")}{sep}models',
        '--add-data', f'{os.path.join(root, "dictionaries")}{sep}dictionaries',
        '--add-data', f'{os.path.join(root, "config", "default_settings.json")}{sep}config',
        '--hidden-import', 'rapidocr_onnxruntime',
        '--hidden-import', 'onnxruntime',
        '--hidden-import', 'cv2',
        '--hidden-import', 'mss',
        '--hidden-import', 'PIL',
        '--hidden-import', 'PIL.Image',
        '--hidden-import', 'deskew',
        '--hidden-import', 'paddleocr',
        '--hidden-import', 'paddlepaddle',
        '--hidden-import', 'paddle',
        '--paths', os.path.join(root),
        '--distpath', dist_dir,
        '--workpath', build_dir,
        '--specpath', os.path.join(root, 'artifacts'),
        os.path.join(root, 'src', 'main.py'),
    ]

    print("執行 PyInstaller...")
    result = subprocess.run(cmd, cwd=root)

    if result.returncode == 0:
        out_exe = os.path.join(dist_dir, 'DesktopOCRTool.exe')
        print(f"\n打包成功！\n輸出：{out_exe}")
    else:
        print(f"\n打包失敗，返回碼：{result.returncode}")
        sys.exit(1)


if __name__ == '__main__':
    main()
