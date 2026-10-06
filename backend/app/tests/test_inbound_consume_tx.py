import pytest
from fastapi import HTTPException

from app.db import connect
from app.main import (
    inbound, LotIn, consume, ConsumeIn, set_item_active, ItemPatch, consumable_items,
)


def _lot_count():
    c = connect()
    n = c.execute("SELECT COUNT(*) n FROM lots").fetchone()["n"]
    c.close()
    return n


def _audit_count():
    c = connect()
    n = c.execute("SELECT COUNT(*) n FROM consumptions").fetchone()["n"]
    c.close()
    return n


def test_inbound_blocked_when_disabled(fresh_db):
    set_item_active(1, ItemPatch(active=False))
    before = _lot_count()
    with pytest.raises(HTTPException) as ei:
        inbound(LotIn(item_id=1, qty=2, expiry="2027-01-01"))
    assert ei.value.status_code == 409 and ei.value.detail == "item_disabled"
    assert _lot_count() == before  # ticket fails as a whole, no lot row

    # Re-enabling allows new inbound again.
    set_item_active(1, ItemPatch(active=True))
    r = inbound(LotIn(item_id=1, qty=2, expiry="2027-01-01"))
    assert _lot_count() == before + 1
    c = connect()
    row = c.execute("SELECT item_id,qty_in,qty_remain,status,expiry FROM lots WHERE id=?", (r["id"],)).fetchone()
    c.close()
    assert tuple(row) == (1, 2, 2, "on_shelf", "2027-01-01")


def test_inbound_unknown_item_404(fresh_db):
    before = _lot_count()
    with pytest.raises(HTTPException) as ei:
        inbound(LotIn(item_id=404, qty=1, expiry="2027-01-01"))
    assert ei.value.status_code == 404 and ei.value.detail == "item_not_found"
    assert _lot_count() == before


def test_disabled_stock_consumed_to_zero(fresh_db):
    # Milk (id=1): two on-shelf lots 2@2026-10-01 and 1@2026-09-28, total 3.
    set_item_active(1, ItemPatch(active=False))
    r = consume(ConsumeIn(item_id=1, qty=3))
    assert r["ok"] and [d["lot_id"] for d in r["deductions"]] == [2, 1]  # earliest expiry first
    c = connect()
    rows = c.execute(
        "SELECT id,status,qty_remain FROM lots WHERE item_id=1 ORDER BY id").fetchall()
    audits = c.execute("SELECT COUNT(*) n FROM consumptions").fetchone()["n"]
    c.close()
    assert all(tuple(x) in {(1, "consumed", 0), (2, "consumed", 0)} for x in rows)
    assert audits == 1
    assert 1 not in {x["item_id"] for x in consumable_items()}

    with pytest.raises(HTTPException) as ei:
        consume(ConsumeIn(item_id=1, qty=1))
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "short"


def test_short_is_atomic(fresh_db):
    c = connect()
    before = [tuple(r) for r in c.execute(
        "SELECT id,status,qty_remain FROM lots WHERE item_id=1 ORDER BY id")]
    c.close()
    audits_before = _audit_count()

    with pytest.raises(HTTPException) as ei:
        consume(ConsumeIn(item_id=1, qty=99))
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "short"

    c = connect()
    after = [tuple(r) for r in c.execute(
        "SELECT id,status,qty_remain FROM lots WHERE item_id=1 ORDER BY id")]
    c.close()
    assert after == before           # whole order failed, remainders unmoved
    assert _audit_count() == audits_before


def test_non_positive_qty_400(fresh_db):
    lots_before, audits_before = _lot_count(), _audit_count()
    for bad in (0, -1):
        with pytest.raises(HTTPException) as ei:
            consume(ConsumeIn(item_id=1, qty=bad))
        assert ei.value.status_code == 400 and ei.value.detail == "qty_non_positive"
    assert _lot_count() == lots_before and _audit_count() == audits_before


def test_dirty_negative_lot_not_drawn(fresh_db):
    # Eggs: +12 lot only; the -3 dirty lot never participates.
    r = consume(ConsumeIn(item_id=2, qty=12))
    assert r["ok"]
    c = connect()
    dirty = c.execute("SELECT qty_remain,status FROM lots WHERE id=5").fetchone()
    good = c.execute("SELECT qty_remain,status FROM lots WHERE id=3").fetchone()
    c.close()
    assert tuple(dirty) == (-3, "on_shelf")
    assert tuple(good) == (0, "consumed")

    with pytest.raises(HTTPException) as ei:
        consume(ConsumeIn(item_id=2, qty=1))
    assert ei.value.status_code == 409
