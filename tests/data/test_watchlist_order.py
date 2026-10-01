"""自选池的「增删改查」在数据层的契约（Task 29）。

页面上的「改」是**排序**（用户 2026-10-01 选定：自选股上下移动），所以这里盯的是：

1. `get_watchlist` 的顺序**由 `sort_order` 决定**，不再是「谁先加谁在前」——
   否则用户挪完顺序，刷新页面又变回去了；
2. 上移/下移是**换位**：第一只上移、最后一只下移都是空操作，不能把票挪丢；
3. 新加的票落在**末尾**，不会插队到用户排好的顺序中间；
4. 老库升级（没有 `sort_order` 列）要能自动补列，且**保持原有顺序**——
   生产库里的 4 只票是按 `added_at` 排的，升级后不能乱。
"""

from __future__ import annotations

import pytest

from chanlun.data import meta


@pytest.fixture()
def conn(tmp_path):
    c = meta.init(tmp_path / "meta.db")
    yield c
    c.close()


def _codes(conn) -> list[str]:
    return [str(r["code"]) for r in meta.get_watchlist(conn)]


def test_new_codes_are_appended_at_the_end(conn):
    for code in ("600000", "600030", "601398"):
        meta.add_watch(conn, code)
    assert _codes(conn) == ["600000", "600030", "601398"]


def test_move_watch_swaps_neighbours(conn):
    for code in ("600000", "600030", "601398"):
        meta.add_watch(conn, code)
    meta.move_watch(conn, "601398", -1)
    assert _codes(conn) == ["600000", "601398", "600030"]
    meta.move_watch(conn, "601398", -1)
    assert _codes(conn) == ["601398", "600000", "600030"]


def test_move_watch_at_the_edges_is_a_noop(conn):
    for code in ("600000", "600030"):
        meta.add_watch(conn, code)
    meta.move_watch(conn, "600000", -1)
    meta.move_watch(conn, "600030", 1)
    assert _codes(conn) == ["600000", "600030"]


def test_move_watch_ignores_unknown_code(conn):
    meta.add_watch(conn, "600000")
    meta.move_watch(conn, "999999", -1)
    assert _codes(conn) == ["600000"]


def test_move_watch_rejects_a_bad_delta(conn):
    meta.add_watch(conn, "600000")
    with pytest.raises(ValueError):
        meta.move_watch(conn, "600000", 2)


def test_add_watch_again_keeps_the_existing_position(conn):
    """重复加同一只票不该把它挪到末尾（页面上是「已在自选」而不是「重新加」）。"""
    for code in ("600000", "600030", "601398"):
        meta.add_watch(conn, code)
    meta.move_watch(conn, "601398", -1)
    meta.add_watch(conn, "601398", "备注")
    assert _codes(conn) == ["600000", "601398", "600030"]


def test_order_column_is_added_to_an_existing_table_and_keeps_order(conn, tmp_path):
    """老库（只有 code/name/added_at/note）升级：补列后顺序必须还是原样。"""
    import sqlite3

    old = tmp_path / "old.db"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE watchlist (code TEXT PRIMARY KEY, name TEXT, added_at TEXT, note TEXT)")
    for i, code in enumerate(("600030", "600519", "000002", "601398")):
        c.execute("INSERT INTO watchlist VALUES (?, '', ?, '')", (code, f"2026-10-01 11:24:4{i}"))
    c.commit()
    c.close()

    up = meta.init(old)
    try:
        assert _codes(up) == ["600030", "600519", "000002", "601398"]
        meta.move_watch(up, "601398", -1)
        assert _codes(up) == ["600030", "600519", "601398", "000002"]
    finally:
        up.close()


def test_seed_watchlist_replaces_the_pool_with_the_defaults(conn):
    """默认自选池 = 主要大盘指数；`--reset` 用它可以一键回到默认。"""
    for code in ("600030", "600519"):
        meta.add_watch(conn, code)
    meta.seed_watchlist(conn, replace=True)
    rows = meta.get_watchlist(conn)
    codes = [str(r["code"]) for r in rows]
    assert codes == [c for c, _ in meta.DEFAULT_WATCHLIST]
    assert "600030" not in codes, "用户选定：默认池只留指数，原来的 4 只股票删掉"
    names = {str(r["code"]): str(r["name"]) for r in rows}
    assert names["sh.000001"] == "上证指数"
    assert names["399006"] == "创业板指"


def test_seed_watchlist_is_idempotent_and_keeps_user_order(conn):
    """不带 replace 时只补缺的，已经在池里的票保持用户排好的位置。"""
    meta.seed_watchlist(conn, replace=True)
    meta.move_watch(conn, "sh.000300", -1)
    before = _codes(conn)
    meta.seed_watchlist(conn, replace=False)
    assert _codes(conn) == before
