"""SQLite helpers. One place that knows where the database lives."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(__import__("os").environ.get("CONSULTBAE_DB", ROOT / "data" / "consultbae.db"))
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def reset_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Drop and recreate. The pipeline is a full rebuild, not an incremental sync."""
    path = Path(db_path or DB_PATH)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    conn = connect(path)
    init_db(conn)
    return conn
