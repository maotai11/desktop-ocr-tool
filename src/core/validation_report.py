"""Small, explicit reports shared by source and frozen acceptance probes."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from contextlib import closing
import sqlite3


def database_integrity(path):
    """Read-only verification must close before the smoke home is removed.

    A SQLite connection context manager controls transactions, not lifetime.
    Explicit close is required on Windows and on error paths too.
    """
    uri = Path(path).absolute().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return connection.execute('PRAGMA integrity_check').fetchone()[0]


def executable_sha256():
    if not getattr(sys, "frozen", False):
        return None
    digest = hashlib.sha256()
    with open(sys.executable, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_validation_report(path, report):
    """Replace complete UTF-8 JSON; never leave a half-written pass."""
    path = Path(path).absolute()
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
