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

    @staticmethod
    def _monitor(monitors, monitor_idx):
        # Index 0 is the virtual desktop in MSS, not a physical monitor.
        # Never silently redirect a stale/invalid selection to another screen.
        if not isinstance(monitor_idx, int) or not 1 <= monitor_idx < len(monitors):
            raise ValueError(f'無效的實體螢幕索引: {monitor_idx}')
        return monitors[monitor_idx]

    def capture_region(self, x: int, y: int, w: int, h: int,
                       monitor_idx: int = 1) -> Optional[np.ndarray]:
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                mon = self._monitor(monitors, monitor_idx)
                if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > mon['width'] or y + h > mon['height']:
                    raise ValueError('框選區域超出所選螢幕；請重新框選')
                region = {
                    'left': mon['left'] + x,
                    'top': mon['top'] + y,
                    'width': w,
                    'height': h,
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
                screenshot = sct.grab(self._monitor(monitors, monitor_idx))
                img = np.array(screenshot)
                return img[:, :, :3]
        except Exception as e:
            logger.error(f"全螢幕截圖失敗: {e}")
            return None

    def get_monitor_info(self, monitor_idx: int = 1) -> dict:
        with mss.mss() as sct:
            monitors = sct.monitors
            return dict(self._monitor(monitors, monitor_idx))

    def close(self):
        pass  # 不再持有 _sct，無需關閉
