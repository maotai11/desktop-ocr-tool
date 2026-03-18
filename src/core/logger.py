# -*- coding: utf-8 -*-
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

_logger_initialized = False


def get_project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def setup_logger():
    global _logger_initialized
    if _logger_initialized:
        return
    root = get_project_root()
    log_dir = os.path.join(root, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'app.log')

    fmt = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(module)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    file_handler = TimedRotatingFileHandler(
        log_path, when='midnight', backupCount=30, encoding='utf-8'
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    _logger_initialized = True


def get_logger(name: str) -> logging.Logger:
    setup_logger()
    return logging.getLogger(name)
