import pytest
from app import seed
from app.db import connect


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    held = connect()  # keep a warm connection so per-call close() doesn't trigger a WAL checkpoint
    yield tmp_path
    held.close()
