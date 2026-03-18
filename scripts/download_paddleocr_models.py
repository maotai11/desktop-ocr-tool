# -*- coding: utf-8 -*-
"""
一鍵下載並複製 PaddleOCR PP-OCRv5 繁體中文模型到專案 models/paddleocr/

執行方式：
    python scripts/download_paddleocr_models.py

下載後目錄結構：
    models/paddleocr/
        det/   <- 文字偵測模型 (PP-OCRv5_server_det)
        rec/   <- 文字辨識模型 (PP-OCRv5_server_rec)
        cls/   <- 文字方向分類模型 (PP-LCNet_x1_0_doc_ori)

完成後應用程式可完全離線使用。
"""
import os
import sys
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PADDLE_DIR = os.path.join(PROJECT_ROOT, 'models', 'paddleocr')
DET_DIR = os.path.join(PADDLE_DIR, 'det')
REC_DIR = os.path.join(PADDLE_DIR, 'rec')
CLS_DIR = os.path.join(PADDLE_DIR, 'cls')

# PaddleX 預設快取目錄（下載後放這裡）
PADDLEX_CACHE = os.path.join(os.path.expanduser('~'), '.paddlex', 'official_models')

# 模型名稱對應
MODEL_MAP = {
    'det': 'PP-OCRv5_server_det',
    'rec': 'PP-OCRv5_server_rec',
    'cls': 'PP-LCNet_x1_0_doc_ori',
}

print("=" * 60)
print("下載 PaddleOCR PP-OCRv5 繁體中文模型")
print(f"目標目錄：{PADDLE_DIR}")
print("=" * 60)

try:
    from paddleocr import PaddleOCR
except ImportError:
    print("[錯誤] paddleocr 未安裝，請先執行：pip install paddleocr")
    sys.exit(1)


def has_model(d):
    return os.path.isfile(os.path.join(d, 'inference.yml'))


# 確認快取是否已存在；若不存在則觸發下載
cache_missing = [
    name for _, name in MODEL_MAP.items()
    if not has_model(os.path.join(PADDLEX_CACHE, name))
]

if cache_missing:
    print(f"\n[1/3] 以下模型尚未快取，觸發下載：{cache_missing}")
    print("      （PaddleOCR 將自動下載到 ~/.paddlex/official_models/）")
    # 不指定 model_dir，讓 PaddleOCR 下載到預設快取
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'False'
    ocr = PaddleOCR(
        use_textline_orientation=True,
        lang='chinese_cht',
        device='cpu',
    )
    import numpy as np
    dummy = np.zeros((64, 256, 3), dtype=np.uint8)
    ocr.ocr(dummy)
    print("      下載完成。")
else:
    print("\n[1/3] 模型已在快取，跳過下載。")

# 複製到專案目錄
print("\n[2/3] 複製模型到專案目錄...")
for key, name in MODEL_MAP.items():
    src = os.path.join(PADDLEX_CACHE, name)
    dst = {'det': DET_DIR, 'rec': REC_DIR, 'cls': CLS_DIR}[key]
    if not has_model(src):
        print(f"[錯誤] 找不到快取模型：{src}")
        sys.exit(1)
    os.makedirs(dst, exist_ok=True)
    for fname in os.listdir(src):
        fpath = os.path.join(src, fname)
        if os.path.isfile(fpath):
            shutil.copy2(fpath, dst)
    print(f"  {name} -> {dst}")

# 驗證
print("\n[3/3] 驗證模型檔案...")
all_ok = True
for label, d in [("偵測", DET_DIR), ("辨識", REC_DIR), ("分類", CLS_DIR)]:
    files = os.listdir(d) if os.path.isdir(d) else []
    size_mb = sum(
        os.path.getsize(os.path.join(d, f))
        for f in files if os.path.isfile(os.path.join(d, f))
    ) / 1024 / 1024
    ok = has_model(d)
    status = "OK" if ok else "FAIL - inference.yml 缺失!"
    print(f"  {label}模型：{d}")
    print(f"    檔案：{files}")
    print(f"    大小：{size_mb:.1f} MB  [{status}]")
    if not ok:
        all_ok = False

if not all_ok:
    print("\n[失敗] 部分模型不完整，請重新執行此腳本。")
    sys.exit(1)

print("\n[完成] 模型已複製至專案目錄，之後啟動無需網路連線。")
