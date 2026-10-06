import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app import seed
from app.db import connect, write_tx
from app.engines.fefo import consume_fefo, expire_lots

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.exception_handler(sqlite3.OperationalError)
def _locked(_: Request, exc: sqlite3.OperationalError):
    # Single-writer contention past busy_timeout: tell the client to retry instead of a bare 500.
    if "locked" in str(exc):
        return JSONResponse(status_code=503, content={"detail": "database_busy"})
    raise exc

def _item(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["active"] = bool(d["active"])
    return d

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items(scope: Literal["active", "all"] = "active"):
    c = connect()
    q = "SELECT id,name,layer,unit,active FROM items"
    if scope == "active":
        q += " WHERE active=1"
    q += " ORDER BY id"
    rows = [_item(r) for r in c.execute(q)]
    c.close()
    return rows

@app.get("/api/consumable-items")
def consumable_items():
    """Dropdown source for consume: every item with positive on-shelf remainder, active or not.

    Same source of truth as /api/fridge (on_shelf lots), so the dropdown totals always
    match the full-shelf numbers; a disabled item disappears here exactly when its last
    lot hits zero and leaves the shelf.
    """
    c = connect()
    rows = [_item(r) for r in c.execute(
        """SELECT items.id AS item_id, items.name, items.layer, items.unit, items.active,
                  ROUND(SUM(lots.qty_remain),3) AS qty_total
           FROM lots JOIN items ON items.id=lots.item_id
           WHERE lots.status='on_shelf' AND lots.qty_remain>0
           GROUP BY lots.item_id ORDER BY items.id""")]
    c.close()
    return rows

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    c = connect()
    q = """SELECT lots.*, items.name, items.layer, items.unit, items.active FROM lots
           JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf'"""
    args = []
    if layer:
        q += " AND items.layer=?"; args.append(layer)
    rows = [_item(r) for r in c.execute(q, args)]
    c.close()
    return rows

@app.get("/api/alerts")
def alerts():
    c = connect()
    warn = int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])
    today = date.today().isoformat()
    rows = [dict(r) for r in c.execute(
        """SELECT lots.*, items.name, items.layer FROM lots JOIN items ON items.id=lots.item_id
           WHERE status='on_shelf' AND qty_remain>0 AND expiry IS NOT NULL""")]
    c.close()
    out = []
    for r in rows:
        if r["expiry"] <= today:
            r["level"] = "expired"
            out.append(r)
        else:
            # simple day diff via fromisoformat
            delta = (date.fromisoformat(r["expiry"]) - date.today()).days
            if delta <= warn:
                r["level"] = "soon"; r["days_left"] = delta; out.append(r)
    return out

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    with write_tx() as c:
        row = c.execute("SELECT active FROM items WHERE id=?", (body.item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "item_not_found")
        if not row["active"]:
            raise HTTPException(409, "item_disabled")  # rollback: no lot row is inserted
        cur = c.execute(
            "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
            (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
        lid = cur.lastrowid
    return {"id": lid}

class ItemPatch(BaseModel):
    active: bool

@app.patch("/api/items/{item_id}")
def set_item_active(item_id: int, body: ItemPatch):
    with write_tx() as c:
        row = c.execute("SELECT active FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "item_not_found")
        target = 1 if body.active else 0
        if row["active"] != target:
            c.execute("UPDATE items SET active=? WHERE id=?", (target, item_id))  # lots untouched
    return {"id": item_id, "active": bool(target)}

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    if body.qty <= 0:
        raise HTTPException(400, "qty_non_positive")
    with write_tx() as c:
        lots = [dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0", (body.item_id,))]
        result = consume_fefo(lots, body.qty)
        if not result["ok"]:
            # short -> rollback: remainders unmoved, no consumption audit row written
            raise HTTPException(409, result)
        for d in result["deductions"]:
            c.execute("UPDATE lots SET qty_remain = qty_remain - ? WHERE id=?", (d["take"], d["lot_id"]))
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?", (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 0:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?", (d["lot_id"],))
        c.execute("INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
                  (body.note, json.dumps(result), datetime.now(timezone.utc).isoformat()))
    return result

@app.post("/api/expire-sweep")
def expire_sweep():
    with write_tx() as c:
        lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
        ids = expire_lots(lots, date.today().isoformat())
        for i in ids:
            c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
