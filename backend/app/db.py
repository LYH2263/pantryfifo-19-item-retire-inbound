import os, sqlite3
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    # concurrent writers (deactivate vs consume) wait in line instead of failing locked
    c.execute("PRAGMA busy_timeout=5000")
    return c

def connect_tx():
    """Autocommit connection for explicit BEGIN IMMEDIATE ... COMMIT/ROLLBACK blocks."""
    c = connect()
    c.isolation_level = None
    return c
