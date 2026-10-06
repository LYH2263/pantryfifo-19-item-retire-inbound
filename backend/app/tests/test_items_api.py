import pytest
from fastapi import HTTPException

from app.db import connect
from app.main import items, consumable_items, fridge, set_item_active, ItemPatch, LotIn, inbound, consume, ConsumeIn


def _qty_by_item():
    """Reference aggregation from the same on-shelf lots the full-shelf list uses."""
    out = {}
    for r in fridge():
        if r["qty_remain"] > 0:  # negative dirty lots show on shelf but are not consumable
            out[r["item_id"]] = round(out.get(r["item_id"], 0) + r["qty_remain"], 3)
    return out


def test_items_scope_filters_disabled(fresh_db):
    assert {r["name"] for r in items()} == {"牛奶", "鸡蛋", "冻饺"}
    assert all(r["active"] is True for r in items())

    set_item_active(3, ItemPatch(active=False))
    assert {r["name"] for r in items()} == {"牛奶", "鸡蛋"}
    all_rows = items("all")
    assert {r["name"] for r in all_rows} == {"牛奶", "鸡蛋", "冻饺"}
    assert [r["active"] for r in all_rows if r["id"] == 3] == [False]


def test_consumable_items_matches_fridge_totals(fresh_db):
    # Seed: milk 2+1, eggs +12 (and a -3 dirty lot), dumpling 1.
    got = {r["item_id"]: r["qty_total"] for r in consumable_items()}
    assert got == _qty_by_item()
    assert got[1] == 3 and got[2] == 12 and got[3] == 1

    # Disabled item with stock stays consumable so it can be drawn down to zero.
    set_item_active(3, ItemPatch(active=False))
    rows = {r["item_id"]: r for r in consumable_items()}
    assert 3 in rows and rows[3]["active"] is False and rows[3]["qty_total"] == 1

    # Draw the disabled item to zero: it leaves both this dropdown and the shelf.
    consume(ConsumeIn(item_id=3, qty=1))
    assert 3 not in {r["item_id"] for r in consumable_items()}
    assert all(r["item_id"] != 3 for r in fridge())

    # Identity holds again after the mutation.
    assert {r["item_id"]: r["qty_total"] for r in consumable_items()} == _qty_by_item()


def test_negative_dirty_lot_not_consumable(fresh_db):
    # The -3 egg lot (id=5) is visible on the shelf but excluded from consumable stock.
    fridge_rows = fridge()
    assert any(r["id"] == 5 and r["qty_remain"] == -3 for r in fridge_rows)
    eggs = next(r for r in consumable_items() if r["item_id"] == 2)
    assert eggs["qty_total"] == 12


def test_patch_active_lifecycle_keeps_lots(fresh_db):
    c = connect()
    before = [tuple(r) for r in c.execute(
        "SELECT id,item_id,qty_in,qty_remain,expiry,status FROM lots ORDER BY id")]
    c.close()

    r = set_item_active(2, ItemPatch(active=False))
    assert r == {"id": 2, "active": False}
    # idempotent no-op
    assert set_item_active(2, ItemPatch(active=False)) == {"id": 2, "active": False}

    with pytest.raises(HTTPException) as ei:
        set_item_active(999, ItemPatch(active=False))
    assert ei.value.status_code == 404 and ei.value.detail == "item_not_found"

    set_item_active(2, ItemPatch(active=True))
    c = connect()
    after = [tuple(r) for r in c.execute(
        "SELECT id,item_id,qty_in,qty_remain,expiry,status FROM lots ORDER BY id")]
    c.close()
    assert after == before  # toggling active never rewrites lots
