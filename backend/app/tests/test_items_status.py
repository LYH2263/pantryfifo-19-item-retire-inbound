"""Item deactivate/re-enable: inbound gating, preview purity, consume atomicity, migration."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import seed
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    return TestClient(app)


def _db(tmp_path):
    conn = sqlite3.connect(tmp_path / "pantryfifo.db")
    conn.row_factory = sqlite3.Row
    return conn


def lot_rows(tmp_path):
    conn = _db(tmp_path)
    n = conn.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"]
    conn.close()
    return n


def lot_row(tmp_path, lot_id):
    conn = _db(tmp_path)
    r = dict(conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone())
    conn.close()
    return r


def test_deactivate_blocks_new_inbound(client, tmp_path):
    before = lot_rows(tmp_path)
    r = client.post("/api/items/1/status", json={"active": 0})
    assert r.status_code == 200 and r.json()["active"] == 0
    r = client.post("/api/lots", json={"item_id": 1, "qty": 5, "expiry": "2027-01-01"})
    assert r.status_code == 409 and r.json()["detail"] == "item_inactive"
    assert lot_rows(tmp_path) == before  # lots 不增行


def test_preissued_inbound_form_fails_at_confirm(client, tmp_path):
    # 预检：表单发出时该品仍在入库下拉
    assert any(i["id"] == 1 for i in client.get("/api/items?active=1").json())
    client.post("/api/items/1/status", json={"active": 0})
    before = lot_rows(tmp_path)
    # 确认：已发出的预检票同样失败，不落行
    r = client.post("/api/lots", json={"item_id": 1, "qty": 5, "expiry": "2027-01-01"})
    assert r.status_code == 409
    assert lot_rows(tmp_path) == before
    # 下拉与确认同源：下拉已无该品，lots 也未增行
    assert all(i["id"] != 1 for i in client.get("/api/items?active=1").json())


def test_preview_leaves_shelf_unchanged(client):
    before = client.get("/api/fridge").json()
    r = client.get("/api/items/1/deactivate-preview")
    assert r.status_code == 200
    body = r.json()
    assert body["item"]["id"] == 1 and body["on_shelf_lots"] == 2 and body["on_shelf_qty"] == 3
    assert client.get("/api/fridge").json() == before  # 全层不变
    assert client.get("/api/items/1/deactivate-preview").json()["item"]["active"] == 1  # 未落停用


def test_deactivated_item_still_consumable_to_zero(client):
    client.post("/api/items/1/status", json={"active": 0})
    r = client.post("/api/consume", json={"item_id": 1, "qty": 3})
    assert r.status_code == 200 and r.json()["ok"]
    left = [x for x in client.get("/api/fridge").json() if x["item_id"] == 1]
    assert left == []  # 在架余量扣到零


def test_failed_deactivate_restores_prior_state(client):
    items_before = client.get("/api/items").json()
    fridge_before = client.get("/api/fridge").json()
    r = client.post("/api/items/999/status", json={"active": 0})
    assert r.status_code == 404
    assert client.get("/api/items").json() == items_before  # 下拉回到操作前
    assert client.get("/api/fridge").json() == fridge_before  # 全层回到操作前


def test_reenable_allows_inbound_without_rewriting_old_expiry(client):
    old = {r["id"]: r["expiry"] for r in client.get("/api/fridge").json() if r["item_id"] == 1}
    client.post("/api/items/1/status", json={"active": 0})
    assert client.post("/api/lots", json={"item_id": 1, "qty": 1, "expiry": "2027-06-01"}).status_code == 409
    client.post("/api/items/1/status", json={"active": 1})
    r = client.post("/api/lots", json={"item_id": 1, "qty": 1, "expiry": "2027-06-01"})
    assert r.status_code == 200  # 改回启用后新入库才允许
    now = {r["id"]: r["expiry"] for r in client.get("/api/fridge").json() if r["item_id"] == 1}
    for lid, exp in old.items():
        assert now[lid] == exp  # 旧在架批到期日不重写


def test_status_rejects_bad_value(client):
    assert client.post("/api/items/1/status", json={"active": 2}).status_code == 400


def test_concurrent_consumes_are_all_or_nothing(client, tmp_path):
    # item 2 在架 12；两笔并发各扣 12：一笔整单成功扣到零，一笔整单失败余量不动
    with ThreadPoolExecutor(max_workers=2) as ex:
        rs = list(ex.map(lambda _: client.post("/api/consume", json={"item_id": 2, "qty": 12}), range(2)))
    assert sum(1 for r in rs if r.status_code == 200) == 1
    assert sum(1 for r in rs if r.status_code == 409) == 1
    won = lot_row(tmp_path, 3)
    assert won["qty_remain"] == 0 and won["status"] == "consumed"
    conn = _db(tmp_path)
    n = conn.execute("SELECT COUNT(*) c FROM consumptions").fetchone()["c"]
    conn.close()
    assert n == 1  # 失败单不留半扣痕迹


def test_deactivate_and_consume_serialize(client, tmp_path):
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [
            ex.submit(client.post, "/api/items/1/status", json={"active": 0}),
            ex.submit(client.post, "/api/consume", json={"item_id": 1, "qty": 3}),
        ]
        rs = [f.result() for f in futs]
    assert rs[0].status_code == 200
    assert rs[1].status_code == 200 and rs[1].json()["ok"]  # 停用不挡在架消费
    assert lot_row(tmp_path, 1)["qty_remain"] == 0
    assert lot_row(tmp_path, 2)["qty_remain"] == 0
    item1 = [i for i in client.get("/api/items").json() if i["id"] == 1][0]
    assert item1["active"] == 0
    # 全层数字与库内一致：item 1 已无在架
    assert all(x["item_id"] != 1 for x in client.get("/api/fridge").json())


def test_migration_adds_active_column_to_old_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    conn = sqlite3.connect(tmp_path / "pantryfifo.db")
    conn.executescript("""
    CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
    INSERT INTO items(name,layer,unit) VALUES ('旧品','mid','个');
    """)
    conn.commit(); conn.close()
    seed.init_db()
    conn = _db(tmp_path)
    row = conn.execute("SELECT * FROM items WHERE name='旧品'").fetchone()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(items)")]
    conn.close()
    assert "active" in cols and row["active"] == 1  # 旧数据默认启用
