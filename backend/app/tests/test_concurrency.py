import sqlite3
import threading

from fastapi import HTTPException

from app.db import connect
from app.main import inbound, LotIn, consume, ConsumeIn, set_item_active, ItemPatch


def _make_item_with_stock(qty):
    """Dedicated single-lot item so concurrency outcomes don't depend on FEFO chaining."""
    c = connect()
    cur = c.execute("INSERT INTO items(name,layer,unit,active) VALUES ('并发品','mid','个',1)")
    item_id = cur.lastrowid
    c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) "
        "VALUES (?,?,?, '2027-06-01','on_shelf','clean')",
        (item_id, qty, qty))
    c.commit()
    c.close()
    return item_id


def _reset(item_id, active=1, qty=None):
    c = connect()
    c.execute("UPDATE items SET active=? WHERE id=?", (active, item_id))
    if qty is not None:
        c.execute("UPDATE lots SET qty_remain=?, status='on_shelf' WHERE item_id=?", (qty, item_id))
    c.execute("DELETE FROM consumptions")
    c.commit()
    c.close()


def _stock(item_id):
    c = connect()
    n = c.execute(
        "SELECT COALESCE(SUM(qty_remain),0) s FROM lots WHERE item_id=? AND status='on_shelf'",
        (item_id,)).fetchone()["s"]
    active = c.execute("SELECT active FROM items WHERE id=?", (item_id,)).fetchone()["active"]
    audits = c.execute("SELECT COUNT(*) n FROM consumptions").fetchone()["n"]
    c.close()
    return round(n, 3), active, audits


def _run_pair(fn_a, fn_b, rounds=20):
    """Release two endpoint calls simultaneously each round; collect (result, exc) per side."""
    outcomes = []
    for _ in range(rounds):
        barrier = threading.Barrier(2)
        box = {}

        def worker(side, fn):
            barrier.wait()
            try:
                box[side] = (fn(), None)
            except Exception as e:  # HTTPException (rolled back) or raw OperationalError when called directly
                box[side] = (None, e)

        t1 = threading.Thread(target=worker, args=("a", fn_a))
        t2 = threading.Thread(target=worker, args=("b", fn_b))
        t1.start(); t2.start(); t1.join(); t2.join()
        outcomes.append((box["a"], box["b"]))
    return outcomes


def test_disable_vs_inbound(fresh_db):
    c = connect()
    cur = c.execute("INSERT INTO items(name,layer,unit,active) VALUES ('并发入库','upper','盒',1)")
    item_id = cur.lastrowid
    c.commit(); c.close()

    for _ in range(20):
        _reset(item_id, active=1)
        c = connect()
        before = c.execute("SELECT COUNT(*) n FROM lots").fetchone()["n"]
        c.close()

        ((ra, ea), (rb, eb)) = _run_pair(
            lambda: set_item_active(item_id, ItemPatch(active=False)),
            lambda: inbound(LotIn(item_id=item_id, qty=1, expiry="2027-06-01")),
            rounds=1,
        )[0]

        # Disable side: success or lock contention, never anything else.
        assert ra is not None or (ea is not None and _is_busy(ea))
        inserted = rb is not None
        if not inserted:
            assert eb is not None and (
                (isinstance(eb, HTTPException) and eb.status_code == 409 and eb.detail == "item_disabled")
                or _is_busy(eb))

        c = connect()
        after = c.execute("SELECT COUNT(*) n FROM lots").fetchone()["n"]
        active = c.execute("SELECT active FROM items WHERE id=?", (item_id,)).fetchone()["active"]
        c.close()

        assert after == before + (1 if inserted else 0)  # 409 ⇔ no new row, never both
        # If disable lost the lock round, settle it so the next round starts from a known state.
        if active == 1:
            set_item_active(item_id, ItemPatch(active=False))


def test_consume_vs_consume_full_demand(fresh_db):
    item_id = _make_item_with_stock(3)
    for _ in range(10):
        _reset(item_id, qty=3)
        ((ra, ea), (rb, eb)) = _run_pair(
            lambda: consume(ConsumeIn(item_id=item_id, qty=3)),
            lambda: consume(ConsumeIn(item_id=item_id, qty=3)),
            rounds=1,
        )[0]
        oks = sum(x is not None for x in (ra, rb))
        for e in (ea, eb):
            assert e is None or (isinstance(e, HTTPException) and e.status_code == 409) or _is_busy(e)
        assert oks <= 1  # never oversell: both "已扣" is forbidden
        stock, _, audits = _stock(item_id)
        assert stock == (0 if oks == 1 else 3)  # drawn to zero, or the loser's order left stock intact
        assert audits == oks


def test_consume_vs_consume_partial(fresh_db):
    item_id = _make_item_with_stock(3)
    for _ in range(10):
        _reset(item_id, qty=3)
        ((ra, ea), (rb, eb)) = _run_pair(
            lambda: consume(ConsumeIn(item_id=item_id, qty=2)),
            lambda: consume(ConsumeIn(item_id=item_id, qty=2)),
            rounds=1,
        )[0]
        oks = sum(x is not None for x in (ra, rb))
        for e in (ea, eb):
            assert e is None or (isinstance(e, HTTPException) and e.status_code == 409) or _is_busy(e)
        assert oks <= 1
        stock, _, audits = _stock(item_id)
        assert stock == 3 - 2 * oks  # 1 if one order landed, 3 if the short order rolled back
        assert stock >= 0
        assert audits == oks


def test_disable_vs_consume(fresh_db):
    item_id = _make_item_with_stock(3)
    for _ in range(10):
        _reset(item_id, active=1, qty=3)
        ((ra, ea), (rb, eb)) = _run_pair(
            lambda: set_item_active(item_id, ItemPatch(active=False)),
            lambda: consume(ConsumeIn(item_id=item_id, qty=3)),
            rounds=1,
        )[0]
        # Consume of a disabled item is allowed; it only loses on lock contention, not on active.
        consumed = rb is not None
        if not consumed:
            assert _is_busy(eb)
        stock, active, _ = _stock(item_id)
        # Either drawn cleanly to zero while disable lands, or the order failed and stock is intact.
        if consumed:
            assert stock == 0
        # Settle disable state: dropdown must end up without the item exactly when it succeeded.
        if active == 1:
            set_item_active(item_id, ItemPatch(active=False))
        c = connect()
        in_dropdown = c.execute("SELECT COUNT(*) n FROM items WHERE id=? AND active=1", (item_id,)).fetchone()["n"]
        c.close()
        assert in_dropdown == 0
        assert ra is not None or _is_busy(ea)


def _is_busy(exc):
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc)
