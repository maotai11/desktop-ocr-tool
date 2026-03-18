# -*- coding: utf-8 -*-
import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_models(project_root: str) -> tuple:
    lock_path = os.path.join(project_root, 'models', 'models.lock.json')
    if not os.path.exists(lock_path):
        logger.warning("models.lock.json 不存在，跳過模型驗證（開發模式）")
        return True, []

    with open(lock_path, 'r', encoding='utf-8') as f:
        lock = json.load(f)

    errors = []
    for model_key, info in lock.items():
        rel_path = info.get('path', '')
        expected_sha = info.get('sha256', '')
        abs_path = os.path.join(project_root, rel_path)

        if not os.path.exists(abs_path):
            errors.append(f"模型檔案不存在: {rel_path}")
            continue
        if expected_sha:
            actual_sha = sha256_file(abs_path)
            if actual_sha != expected_sha:
                errors.append(f"模型 SHA256 不符: {rel_path}")
                logger.error(f"模型校驗失敗 {rel_path}")
            else:
                logger.info(f"模型校驗通過: {rel_path}")

    return len(errors) == 0, errors
