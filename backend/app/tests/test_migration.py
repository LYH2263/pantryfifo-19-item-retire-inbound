from app.db import connect
from app import seed


def test_fresh_schema_has_active(fresh_db):
    c = connect()
    cols = [r["name"] for r in c.execute("PRAGMA table_info(items)")]
    assert "active" in cols
    actives = [r["active"] for r in c.execute("SELECT active FROM items ORDER BY id")]
    assert actives == [1, 1, 1]
    c.close()
    # idempotent: re-running init_db changes nothing
    seed.init_db()
    c = connect()
    assert c.execute("SELECT COUNT(*) n FROM items").fetchone()["n"] == 3
    c.close()


def test_legacy_db_alter_idempotent(tmp_path, monkeypatch):
    # A DATA_DIR with no DB yet: build the pre-migration schema by hand,
    # populated so the seed branch is skipped.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    c = connect()
    c.execute("PRAGMA journal_mode=DELETE")  # legacy databases predate the WAL switch
    c.executescript("""
    CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
    CREATE TABLE lots(
      id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, qty_in REAL, qty_remain REAL,
      expiry TEXT, status TEXT, data_quality TEXT
    );
    CREATE TABLE consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, result_json TEXT, created_at TEXT);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    INSERT INTO items(name,layer,unit) VALUES ('legacy','upper','盒');
    INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality)
      VALUES (1, 7, 4, '2026-05-01', 'on_shelf', 'clean');
    """)
    c.commit()
    c.close()

    seed.init_db()

    c = connect()
    cols = [r["name"] for r in c.execute("PRAGMA table_info(items)")]
    assert "active" in cols
    r = c.execute("SELECT name, active FROM items WHERE id=1").fetchone()
    assert r["name"] == "legacy" and r["active"] == 1
    assert c.execute("SELECT COUNT(*) n FROM items").fetchone()["n"] == 1  # no seed on non-empty DB
    lot = c.execute("SELECT qty_in, qty_remain, expiry FROM lots WHERE id=1").fetchone()
    assert tuple(lot) == (7, 4, "2026-05-01")  # existing lots untouched
    c.close()

    seed.init_db()  # second run must be a no-op


def test_wal_persists(fresh_db):
    c = connect()
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    c.close()
