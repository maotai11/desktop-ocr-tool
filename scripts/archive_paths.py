"""Shared ZIP-name boundary; never silently repair untrusted archive paths.

ZIP members use forward slashes. Native filesystem and PyInstaller CArchive
paths have different contracts and must not be passed through this validator.
"""
from pathlib import PurePosixPath, PureWindowsPath


def validate_zip_names(infos):
    seen = set()
    for info in infos:
        name = info.orig_filename
        if name != info.filename:
            raise ValueError('Normalized archive member name')
        plain = name[:-1] if name.endswith('/') else name
        parts = plain.split('/')
        if (not plain or '\\' in plain or any(ord(c) < 32 for c in plain)
                or PureWindowsPath(plain).drive or PurePosixPath(plain).is_absolute()
                or PurePosixPath(plain).as_posix() != plain
                or any(p in ('', '.', '..') for p in parts)):
            raise ValueError('Unsafe bundle member path')
        if any(p.endswith((' ', '.')) or any(c in p for c in ':<>"|?*')
               or p.split('.')[0].upper() in
               {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                *(f'LPT{i}' for i in range(1, 10))} for p in parts):
            raise ValueError('Unsafe Windows bundle member path')
        key = plain.casefold()
        if key in seen:
            raise ValueError('Duplicate bundle member after case folding')
        seen.add(key)
