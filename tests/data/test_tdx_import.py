"""通达信整包导入的测试（全部离线，用合成 .day 文件）。"""

from __future__ import annotations

import struct

import pytest

from chanlun.data import meta, store, tdx


def _rec(date, close_cents):
    return struct.pack(
        "<IIIIIfII", date, close_cents, close_cents, close_cents, close_cents, 1e6, 1000, 0
    )


def _write(root, market, code, dates):
    d = root / market / "lday"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{market}{code}.day").write_bytes(
        b"".join(_rec(x, 1000 + i) for i, x in enumerate(dates))
    )


@pytest.fixture()
def src(tmp_path):
    root = tmp_path / "raw"
    for market, code, dates in (
        ("sh", "600000", (20260102, 20260105)),
        ("sh", "000300", (20260102, 20260105)),  # 指数
        ("sz", "000001", (20260102, 20260105)),
        ("bj", "430017", (20260102, 20260105)),
        ("sh", "880001", (20260102, 20260105)),  # 板块指数：默认不导入
        ("sh", "510050", (20260102, 20260105)),  # ETF：默认不导入
    ):
        _write(root, market, code, dates)
    _write(root, "sz", "000002", ())  # 空文件：跳过并计数
    return root


@pytest.fixture()
def alias_src(tmp_path):
    """北交所改号：老码 830799 与新码 920799 重叠 25 天且收盘价逐日相同。"""
    root = tmp_path / "alias_raw"
    days = [20260101 + i for i in range(1, 32)]
    close = {d: 2000 + i for i, d in enumerate(days)}
    for code, use in (("830799", days[:25]), ("920799", days)):
        d = root / "bj" / "lday"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"bj{code}.day").write_bytes(b"".join(_rec(x, close[x]) for x in use))
    return root


@pytest.fixture()
def conn(tmp_path):
    """走真实的 `meta.init`（含 row_factory 与建表），别用裸连接假装。"""
    return meta.init(tmp_path / "meta.db")


def test_scan_dir_default_excludes_boards_and_funds(src):
    keys = {s.key for s in tdx.scan_dir(src)}
    assert keys == {"600000", "sh.000300", "000001", "430017", "000002"}


def test_scan_dir_can_include_everything(src):
    keys = {s.key for s in tdx.scan_dir(src, kinds=("stock", "index", "board", "fund"))}
    assert "880001" in keys and "510050" in keys


def test_scan_dir_records_kind(src):
    kinds = {(s.key, s.kind) for s in tdx.scan_dir(src, kinds=("stock", "index", "board"))}
    assert ("600000", "stock") in kinds and ("sh.000300", "index") in kinds
    assert ("880001", "board") in kinds


def test_import_dir_writes_store_and_sync_state(src, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    stats = tdx.import_dir(src, conn)
    assert (stats.ok, stats.failed, stats.skipped_empty) == (4, 0, 1)
    assert stats.rows == 8
    assert dict(stats.newest_by_market) == {
        "sh": "2026-01-05",
        "sz": "2026-01-05",
        "bj": "2026-01-05",
    }
    assert len(store.read("600000", "day")) == 2
    assert len(store.read("sh.000300", "day")) == 2
    row = meta.get_sync(conn, "sh.000300", "day")
    assert (row["start_ts"], row["end_ts"], row["rows"], row["adjust"], row["error"]) == (
        "2026-01-02", "2026-01-05", 2, "raw", None)


def test_import_dir_is_idempotent(src, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    tdx.import_dir(src, conn)
    again = tdx.import_dir(src, conn)
    assert again.ok == 4 and again.rows == 8
    assert len(store.read("600000", "day")) == 2


def test_import_dir_records_failure_without_aborting(src, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    _write(src, "sh", "600001", ())
    (src / "sh" / "lday" / "sh600001.day").write_bytes(b"\x00" * 5)  # 截断
    stats = tdx.import_dir(src, conn)
    assert (stats.ok, stats.failed) == (4, 1)
    assert stats.errors[0][0] == "600001"
    assert "整数倍" in stats.errors[0][1]
    sync = meta.get_sync(conn, "600001", "day")
    assert sync is None or sync["error"]


def test_import_dir_dry_run_touches_nothing(src, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    stats = tdx.import_dir(src, conn, dry_run=True)
    assert stats.ok == 4 and stats.rows == 8
    assert not (tmp_path / "data").exists()


def test_detect_bj_aliases_requires_price_agreement(alias_src):
    assert tdx.detect_bj_aliases(alias_src) == {"830799": "920799"}


def test_detect_bj_aliases_rejects_mismatched_prices(alias_src):
    (alias_src / "bj" / "lday" / "bj920799.day").write_bytes(
        b"".join(_rec(20260101 + i, 9000 + i) for i in range(1, 32))
    )
    assert tdx.detect_bj_aliases(alias_src) == {}


def test_detect_bj_aliases_needs_enough_overlap(alias_src):
    (alias_src / "bj" / "lday" / "bj830799.day").write_bytes(
        b"".join(_rec(20260101 + i, 2000 + i) for i in range(1, 6))
    )
    assert tdx.detect_bj_aliases(alias_src) == {}


def test_import_dir_merges_renumbered_bj_codes(alias_src, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    stats = tdx.import_dir(alias_src, conn)
    assert stats.ok == 1 and stats.skipped_alias == 1
    assert stats.aliased == (("830799", "920799"),)
    assert len(store.read("920799", "day")) == 31
    assert store.read("830799", "day").empty
    assert meta.get_alias(conn, "830799") == "920799"


def test_import_dir_skips_empty_files(src, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    tdx.import_dir(src, conn)
    assert meta.get_sync(conn, "000002", "day") is None
    assert store.read("000002", "day").empty


def test_import_dir_can_limit_to_one_market(src, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    stats = tdx.import_dir(src, conn, markets=("sz",))
    assert stats.ok == 1
    assert dict(stats.newest_by_market) == {"sz": "2026-01-05"}
    assert store.read("600000", "day").empty
