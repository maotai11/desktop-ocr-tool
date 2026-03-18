# -*- coding: utf-8 -*-
"""
產生 models/models.lock.json - 計算所有模型 SHA256
執行：python scripts/generate_lock.py
"""
import hashlib
import json
import os
import sys


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    models = {
        'det': {
            'path': 'models/det/pp-ocrv4_det.onnx',
            'version': 'pp-ocrv4'
        },
        'rec': {
            'path': 'models/rec/pp-ocrv4_rec.onnx',
            'version': 'pp-ocrv4'
        },
        'cls': {
            'path': 'models/cls/pp-ocrv4_cls.onnx',
            'version': 'pp-ocrv4'
        },
    }

    lock = {}
    for key, info in models.items():
        abs_path = os.path.join(root, info['path'])
        if not os.path.exists(abs_path):
            print(f"警告：{info['path']} 不存在，跳過")
            continue
        sha = sha256_file(abs_path)
        size = os.path.getsize(abs_path)
        lock[key] = {
            'path': info['path'],
            'sha256': sha,
            'version': info['version'],
            'size_bytes': size,
        }
        print(f"{key}: {sha[:16]}... ({size:,} bytes)")

    lock_path = os.path.join(root, 'models', 'models.lock.json')
    with open(lock_path, 'w', encoding='utf-8') as f:
        json.dump(lock, f, indent=2)
    print(f"\n已寫入 {lock_path}")


if __name__ == '__main__':
    main()
