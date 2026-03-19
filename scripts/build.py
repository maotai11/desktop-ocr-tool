# -*- coding: utf-8 -*-
"""
打包腳本：使用 PyInstaller 打包為單一 EXE（離線可用）
執行：python scripts/build.py

注意事項：
- ONNX 模型（RapidOCR PP-OCRv4）會一併打包進 EXE
- PP-OCRv5 模型：請先執行 scripts\\download_paddleocr_models.bat 再打包
- 打包後 EXE 可在乾淨 Windows 離線機直接執行，不需要 Python
"""
import sys
import os
import subprocess
import json
import hashlib
import shutil


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

    # --- 驗證 RapidOCR ONNX 模型 ---
    lock_path = os.path.join(root, 'models', 'models.lock.json')
    if os.path.exists(lock_path):
        with open(lock_path, encoding='utf-8') as f:
            lock = json.load(f)
        print("驗證 RapidOCR 模型 SHA256...")
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
            print(f"  OK {info['path']}")
    else:
        print("警告：models.lock.json 不存在，跳過模型驗證")

    # --- 偵測 PP-OCRv5 模型 ---
    paddle_dir = os.path.join(root, 'models', 'paddleocr')

    def has_paddle():
        return (
            os.path.isfile(os.path.join(paddle_dir, 'det', 'inference.yml')) and
            os.path.isfile(os.path.join(paddle_dir, 'rec', 'inference.yml')) and
            os.path.isfile(os.path.join(paddle_dir, 'cls', 'inference.yml'))
        )

    if has_paddle():
        print("OK PP-OCRv5 本機模型已就緒，一併打包進 EXE")
    else:
        print("WARNING models/paddleocr/ 未就緒 -> EXE 啟動時使用 RapidOCR PP-OCRv4 fallback")
        print("  （如要 PP-OCRv5：先執行 scripts\\download_paddleocr_models.bat 再重打包）")

    # --- 建立輸出目錄 ---
    sep = os.pathsep
    dist_dir = os.path.join(root, 'artifacts', 'dist')
    build_dir = os.path.join(root, 'artifacts', 'build')
    os.makedirs(dist_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)

    # --- 組裝 PyInstaller 指令 ---
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--windowed',
        '--name', 'DesktopOCRTool',
        # 打包進 EXE 的資料
        '--add-data', f'{os.path.join(root, "models")}{sep}models',
        '--add-data', f'{os.path.join(root, "dictionaries")}{sep}dictionaries',
        '--add-data', f'{os.path.join(root, "config", "default_settings.json")}{sep}config',
        # 路徑（確保 import 解析正確）
        '--paths', root,
        # Hidden imports — 核心
        '--hidden-import', 'src',
        '--hidden-import', 'src.app',
        '--hidden-import', 'src.core',
        '--hidden-import', 'src.data',
        '--hidden-import', 'src.ui',
        '--hidden-import', 'src.workers',
        '--hidden-import', 'src.ocr',
        '--hidden-import', 'src.capture',
        '--hidden-import', 'src.clipboard',
        # NumPy 2.x 完整收集（numpy._core C extension 必須用 collect-all）
        '--collect-all', 'numpy',
        # RapidOCR — config.yaml 等資料檔（collect-data 只收資料，不拉模組依賴）
        '--collect-data', 'rapidocr_onnxruntime',
        '--hidden-import', 'rapidocr_onnxruntime',
        '--hidden-import', 'onnxruntime',
        '--hidden-import', 'onnxruntime.capi',
        '--collect-all', 'onnxruntime',
        '--hidden-import', 'cv2',
        '--hidden-import', 'PIL',
        '--hidden-import', 'PIL.Image',
        '--hidden-import', 'PIL.ImageOps',
        '--hidden-import', 'PIL.ImageFilter',
        '--hidden-import', 'mss',
        '--hidden-import', 'mss.windows',
        '--hidden-import', 'deskew',
        '--hidden-import', 'scipy',
        '--hidden-import', 'scipy.fft',
        # PaddleOCR + PaddleX — 只收 YAML/JSON pipeline 設定檔（collect-data 不拉整棵依賴樹）
        '--collect-data', 'paddleocr',
        '--collect-data', 'paddlex',
        # PaddleOCR/PaddleX pipeline 類別（frozen EXE 需要這些模組才能 create_pipeline）
        '--collect-submodules', 'paddleocr._pipelines',
        '--collect-submodules', 'paddlex.inference.pipelines',
        '--hidden-import', 'paddleocr',
        '--hidden-import', 'paddle',
        '--hidden-import', 'paddle.base',
        # Hidden imports — PySide6 外掛
        '--hidden-import', 'PySide6.QtCore',
        '--hidden-import', 'PySide6.QtGui',
        '--hidden-import', 'PySide6.QtWidgets',
        # 輸出設定
        '--distpath', dist_dir,
        '--workpath', build_dir,
        '--specpath', os.path.join(root, 'artifacts'),
        os.path.join(root, 'src', 'main.py'),
    ]

    print("\n執行 PyInstaller...")
    result = subprocess.run(cmd, cwd=root)

    if result.returncode != 0:
        print(f"\n打包失敗，返回碼：{result.returncode}")
        sys.exit(1)

    out_exe = os.path.join(dist_dir, 'DesktopOCRTool.exe')
    exe_mb = os.path.getsize(out_exe) / 1024 / 1024
    print(f"\nOK 打包成功！")
    print(f"  EXE：{out_exe}  ({exe_mb:.1f} MB)")

    # --- 建立發佈 ZIP ---
    zip_name = 'DesktopOCRTool-v1.0.0'
    zip_dir  = os.path.join(root, 'artifacts', zip_name)
    zip_path = os.path.join(root, 'artifacts', f'{zip_name}.zip')

    if os.path.exists(zip_dir):
        shutil.rmtree(zip_dir)
    os.makedirs(zip_dir)

    # 把 EXE 複製進資料夾
    shutil.copy2(out_exe, zip_dir)

    # 建立簡易 README
    readme = os.path.join(zip_dir, 'README.txt')
    with open(readme, 'w', encoding='utf-8') as f:
        f.write("桌面 OCR 擷取工具 v1.0.0\n")
        f.write("=" * 40 + "\n\n")
        f.write("使用方式：\n")
        f.write("  直接雙擊 DesktopOCRTool.exe 啟動\n\n")
        f.write("首次啟動：\n")
        f.write("  - 系統匣圖示 + 浮動視窗自動出現\n")
        f.write("  - data/ config/ logs/ 自動建立於 EXE 旁\n\n")
        f.write("熱鍵：\n")
        f.write("  Ctrl+Shift+O  框選 OCR\n")
        f.write("  Ctrl+Shift+S  框選截圖\n")
        f.write("  Ctrl+Shift+F  全螢幕截圖\n")
        f.write("  Ctrl+Shift+Space  顯示/隱藏浮動窗\n")
        f.write("  Ctrl+Shift+M  開啟主控台\n\n")
        f.write("系統需求：Windows 10/11 x64\n")
        f.write("無需安裝 Python 或任何套件\n")

    if os.path.exists(zip_path):
        os.remove(zip_path)
    shutil.make_archive(
        os.path.join(root, 'artifacts', zip_name),
        'zip',
        os.path.join(root, 'artifacts'),
        zip_name,
    )

    zip_mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"  ZIP：{zip_path}  ({zip_mb:.1f} MB)")
    print(f"\n內容物：")
    print(f"  {zip_name}/DesktopOCRTool.exe")
    print(f"  {zip_name}/README.txt")
    print(f"\n解壓縮後直接執行 DesktopOCRTool.exe 即可，無需網路/Python。")


if __name__ == '__main__':
    main()
