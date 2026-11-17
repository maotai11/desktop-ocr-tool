# -*- coding: utf-8 -*-
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from .resource_budget import disk_quota_guard, register_managed_root, ResourceBudgetExceeded, _storage_lock, disk_write_reservation
from .config import get_project_root  # single authoritative definition

_logger_initialized = False


class QuotaRotatingHandler(RotatingFileHandler):
    """Keep file logging bounded; never recurse or discard old data to admit it."""
    def _open(self):
        # The byte admission must equal the actual UTF-8 write on Windows too.
        # TextIOWrapper's default CRLF translation would add unreserved bytes.
        return open(self.baseFilename, self.mode, encoding=self.encoding,
                    errors=self.errors, newline='')

    def emit(self, record):
        # Logging takes its handler lock before emit. Never wait for a writer
        # which may itself be trying to log while holding the quota lock.
        if not _storage_lock.acquire(blocking=False):
            return
        try:
            message = self.format(record)
            # A malicious/accidental huge message cannot allocate a huge log file.
            encoded = (message[:8192] + ('…' if len(message) > 8192 else '') + '\n').encode('utf-8')
            with disk_quota_guard(reserve_bytes=disk_write_reservation(self.baseFilename, len(encoded), new_directories=1)):
                if self.stream is None:
                    directory = os.path.dirname(self.baseFilename)
                    if not os.path.isdir(os.path.dirname(directory)):
                        return
                    os.makedirs(directory, exist_ok=True)
                    self.stream = self._open()
                self.stream.seek(0, 2)
                if self.stream.tell() + len(encoded) >= self.maxBytes:
                    self.doRollover()
                    self.stream = self._open()
                self.stream.write(encoded.decode('utf-8'))
                self.flush()
        except (OSError, ResourceBudgetExceeded):
            # Console logging survives a full/unreadable managed volume.
            return
        finally:
            _storage_lock.release()


def setup_logger():
    global _logger_initialized
    if _logger_initialized:
        return
    root = get_project_root()
    log_dir = os.path.join(root, 'logs')
    log_path = os.path.join(log_dir, 'app.log')

    fmt = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(module)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    file_handler = QuotaRotatingHandler(
        log_path, maxBytes=512 * 1024, backupCount=3, encoding='utf-8', delay=True
    )
    register_managed_root(log_dir, owner=file_handler)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # 抑制 Pillow 的 DEBUG chunk log（PNG IHDR/IDAT 等）
    logging.getLogger('PIL').setLevel(logging.WARNING)

    _logger_initialized = True


def get_logger(name: str) -> logging.Logger:
    setup_logger()
    return logging.getLogger(name)
