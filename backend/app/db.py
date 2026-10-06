import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    c = sqlite3.connect(db_path(), timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA synchronous=NORMAL")  # WAL-safe: no per-commit fsync, no corruption risk
    return c

@contextmanager
def write_tx():
    """Serialize all writers: take the RESERVED lock up front, commit/rollback as a unit."""
    c = connect()
    c.isolation_level = None  # driver-managed deferred BEGIN would clash with our explicit BEGIN
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
