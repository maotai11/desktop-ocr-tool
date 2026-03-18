# -*- coding: utf-8 -*-
import logging
from typing import Optional
import mss
import numpy as np

logger = logging.getLogger(__name__)


class MssBackend:
    """
    每次截圖都建新的 mss() 實例（with 語句），避免 thread-local srcdc 問題。
    mss 的 Win32 DC 是 thread-local，CaptureWorker 在 QThread 中呼叫時
    不能共用主執行緒建立的 _sct。
    """

    def get_monitors(self) -> list:
        with mss.mss() as sct:
            return list(sct.monitors[1:])

    def capture_region(self, x: int, y: int, w: int, h: int,
                       monitor_idx: int = 1) -> Optional[np.ndarray]:
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if monitor_idx >= len(monitors):
                    monitor_idx = 1
                mon = monitors[monitor_idx]
                region = {
                    'left': mon['left'] + x,
                    'top': mon['top'] + y,
                    'width': max(w, 1),
                    'height': max(h, 1),
                }
                screenshot = sct.grab(region)
                img = np.array(screenshot)
                return img[:, :, :3]  # BGR, drop alpha
        except Exception as e:
            logger.error(f"截圖區域失敗: {e}")
            return None

    def capture_monitor(self, monitor_idx: int = 1) -> Optional[np.ndarray]:
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if monitor_idx >= len(monitors):
                    monitor_idx = 1
                screenshot = sct.grab(monitors[monitor_idx])
                img = np.array(screenshot)
                return img[:, :, :3]
        except Exception as e:
            logger.error(f"全螢幕截圖失敗: {e}")
            return None

    def get_monitor_info(self, monitor_idx: int = 1) -> dict:
        with mss.mss() as sct:
            monitors = sct.monitors
            if monitor_idx < len(monitors):
                return dict(monitors[monitor_idx])
        return {'left': 0, 'top': 0, 'width': 1920, 'height': 1080}

    def close(self):
        pass  # 不再持有 _sct，無需關閉
