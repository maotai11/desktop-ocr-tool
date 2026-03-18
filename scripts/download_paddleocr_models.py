# -*- coding: utf-8 -*-
"""
一鍵下載 PaddleOCR PP-OCRv5 繁體中文模型到專案 models/paddleocr/

執行方式：
    python scripts/download_paddleocr_models.py

下載後目錄結構：
    models/paddleocr/
        det/   ← 文字偵測模型 (PP-OCRv5)
        rec/   ← 文字辨識模型 (繁體中文)
        cls/   ← 文字方向分類模型

完成後應用程式可完全離線使用。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PADDLE_DIR = os.path.join(PROJECT_ROOT, 'models', 'paddleocr')
DET_DIR = os.path.join(PADDLE_DIR, 'det')
REC_DIR = os.path.join(PADDLE_DIR, 'rec')
CLS_DIR = os.path.join(PADDLE_DIR, 'cls')

# 允許下載（不繞過連線檢查）
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'False'
# 注意：不預建空資料夾，讓 PaddleOCR 自行建立並下載

print("=" * 60)
print("下載 PaddleOCR PP-OCRv5 繁體中文模型")
print(f"目標目錄：{PADDLE_DIR}")
print("=" * 60)

try:
    from paddleocr import PaddleOCR
except ImportError:
    print("[錯誤] paddleocr 未安裝，請先執行：pip install paddleocr")
    sys.exit(1)

print("\n[1/3] 初始化引擎（將自動下載缺失模型）...")
ocr = PaddleOCR(
    use_angle_cls=True,
    lang='chinese_cht',
    device='cpu',
    det_model_dir=DET_DIR,
    rec_model_dir=REC_DIR,
    cls_model_dir=CLS_DIR,
)

print("\n[2/3] 執行暖機推論確認模型正常...")
import numpy as np
dummy = np.zeros((64, 256, 3), dtype=np.uint8)
result = ocr.ocr(dummy, cls=True)
print(f"暖機結果：{result}")

print("\n[3/3] 檢查模型檔案...")
for label, d in [("偵測", DET_DIR), ("辨識", REC_DIR), ("分類", CLS_DIR)]:
    files = os.listdir(d) if os.path.isdir(d) else []
    size_mb = sum(
        os.path.getsize(os.path.join(d, f))
        for f in files if os.path.isfile(os.path.join(d, f))
    ) / 1024 / 1024
    print(f"  {label}模型：{d}")
    print(f"    檔案：{files}")
    print(f"    大小：{size_mb:.1f} MB")

print("\n[完成] 模型已下載至專案目錄，之後啟動無需網路連線。")
