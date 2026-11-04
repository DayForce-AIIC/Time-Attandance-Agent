"""Initialize, seed, and detect the ShiftGuard demo database."""

# This script also supports direct invocation from outside the backend directory.
# ruff: noqa: E402

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clock import Clock
from app.config import get_settings
from app.db import get_conn, init_db
from app.detection import run_detection
from app.policies import load_policies
from app.seed import seed


def main() -> None:
    """Create the database, insert demo records, and run initial detection."""
    settings = get_settings()
    conn = get_conn(settings.db_path)
    try:
        init_db(conn)
        seed(conn)
        result = run_detection(conn, Clock(settings).now(), load_policies())
        print(result.model_dump_json())
    finally:
        conn.close()


if __name__ == "__main__":
    main()
