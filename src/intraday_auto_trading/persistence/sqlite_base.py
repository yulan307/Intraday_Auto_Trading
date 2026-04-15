from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3


def ensure_parent_dir(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    ensure_parent_dir(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def to_storage_ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")

