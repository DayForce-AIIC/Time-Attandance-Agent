"""SQLite connection and schema lifecycle helpers."""

import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def get_conn(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection configured for row access and foreign keys."""
    database = str(path)
    is_uri = database.startswith("file:")
    if database != ":memory:" and not is_uri:
        db_path = Path(database).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        database = str(db_path)
    conn = sqlite3.connect(database, check_same_thread=False, uri=is_uri)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema and audit immutability triggers if they are absent."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def reset_db(path: str | Path) -> sqlite3.Connection:
    """Recreate a database at path and return its initialized connection."""
    database = str(path)
    if database != ":memory:":
        db_path = Path(database).expanduser()
        if db_path.exists():
            db_path.unlink()
    conn = get_conn(database)
    init_db(conn)
    return conn
