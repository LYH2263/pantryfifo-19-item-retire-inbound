import json
from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect, connect_tx
from app.engines.fefo import consume_fefo, expire_lots

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items(active: int | None = None):
    c = connect()
    q = "SELECT * FROM items"
    args = []
    if active is not None:
        q += " WHERE active=?"; args.append(active)
    rows = [dict(r) for r in c.execute(q, args)]; c.close(); return rows

class StatusIn(BaseModel):
    active: int

@app.post("/api/items/{item_id}/status")
def set_item_status(item_id: int, body: StatusIn):
    # single atomic UPDATE: only flips items.active, never rewrites lots
    if body.active not in (0, 1):
        raise HTTPException(400, "active_must_be_0_or_1")
    c = connect()
    cur = c.execute("UPDATE items SET active=? WHERE id=?", (body.active, item_id))
    if cur.rowcount != 1:
        c.close(); raise HTTPException(404, "item")
    c.commit()
    row = dict(c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())
    c.close(); return row

@app.get("/api/items/{item_id}/deactivate-preview")
def deactivate_preview(item_id: int):
    # read-only impact summary for the confirm dialog; shelf state is untouched
    c = connect()
    item = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        c.close(); raise HTTPException(404, "item")
    agg = c.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(qty_remain),0) qty FROM lots
           WHERE item_id=? AND status='on_shelf' AND qty_remain>0""", (item_id,)).fetchone()
    c.close()
    return {"item": dict(item), "on_shelf_lots": agg["n"], "on_shelf_qty": agg["qty"]}

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    c = connect()
    q = """SELECT lots.*, items.name, items.layer, items.unit, items.active FROM lots
           JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf'"""
    args = []
    if layer:
        q += " AND items.layer=?"; args.append(layer)
    rows = [dict(r) for r in c.execute(q, args)]; c.close(); return rows

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
    # confirm-time gate: any pre-issued inbound form fails here once the item is
    # deactivated, so lots never gain a row the dropdown no longer offers
    c = connect_tx()
    try:
        c.execute("BEGIN IMMEDIATE")
        item = c.execute("SELECT id, active FROM items WHERE id=?", (body.item_id,)).fetchone()
        if not item:
            c.execute("ROLLBACK"); raise HTTPException(404, "item")
        if not item["active"]:
            c.execute("ROLLBACK"); raise HTTPException(409, "item_inactive")
        cur = c.execute(
            "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
            (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
        c.execute("COMMIT")
        return {"id": cur.lastrowid}
    finally:
        c.close()

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    # one write-locked transaction: deduct to zero or fail the whole order with
    # shelf quantities untouched; deactivation serializes against the same lock
    c = connect_tx()
    try:
        c.execute("BEGIN IMMEDIATE")
        lots = [dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0", (body.item_id,))]
        result = consume_fefo(lots, body.qty)
        if not result["ok"] and result["reason"] == "qty_non_positive":
            c.execute("ROLLBACK"); raise HTTPException(400, result["reason"])
        if not result["ok"]:
            c.execute("ROLLBACK"); raise HTTPException(409, result)
        remain_by_id = {l["id"]: float(l["qty_remain"]) for l in lots}
        for d in result["deductions"]:
            cur = c.execute(
                "UPDATE lots SET qty_remain = qty_remain - ? WHERE id=? AND qty_remain >= ?",
                (d["take"], d["lot_id"], d["take"]))
            if cur.rowcount != 1:
                c.execute("ROLLBACK")
                raise HTTPException(409, {"ok": False, "reason": "concurrent_update", "deductions": [], "short": 0.0})
            if remain_by_id.get(d["lot_id"], 0.0) - d["take"] <= 1e-9:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?", (d["lot_id"],))
        c.execute("INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
                  (body.note, json.dumps(result), datetime.now(timezone.utc).isoformat()))
        c.execute("COMMIT")
        return result
    finally:
        c.close()

@app.post("/api/expire-sweep")
def expire_sweep():
    c = connect()
    lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
    ids = expire_lots(lots, date.today().isoformat())
    for i in ids:
        c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    c.commit(); c.close(); return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
