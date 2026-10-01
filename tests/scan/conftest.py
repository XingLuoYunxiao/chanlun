"""`tests/scan` 的公共夹具：把 store 与 meta 都关进临时目录。

为什么必须这么做：扫描器默认会去 `config.data.root` 与 `meta_db` 落盘，
一旦夹具漏改，测试就会读写真实 `data/`、把测试命中灌进生产 scan_result 表。
这里统一 monkeypatch `store.DATA_ROOT`（模块全局，子进程由任务参数另行传递）。
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from chanlun.data import meta, store


class Env:
    def __init__(self, root, conn: sqlite3.Connection):
        self.root = root
        self.conn = conn

    # ---- parquet ----
    def write(self, code: str, period: str, df: pd.DataFrame) -> None:
        store.write(code, period, df)

    def corrupt(self, code: str, period: str) -> None:
        """写一个坏 parquet：读的时候抛异常，用于验证「单只失败不拖垮全局」。"""
        path = store.path_for(code, period)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"this-is-not-parquet")

    def drop(self, code: str, period: str) -> None:
        store.path_for(code, period).unlink(missing_ok=True)

    # ---- meta ----
    def sync(self, code: str, period: str, df: pd.DataFrame | None = None, *,
             start_ts: str | None = None, end_ts: str | None = None,
             rows: int | None = None, error: str | None = None) -> None:
        if df is not None:
            start_ts = start_ts or str(df["ts"].iloc[0])
            end_ts = end_ts or str(df["ts"].iloc[-1])
            rows = len(df) if rows is None else rows
        meta.set_sync(self.conn, code, period, start_ts, end_ts, rows or 0, "qfq", error)

    def name(self, code: str, name: str, market: str = "sh") -> None:
        meta.upsert_universe(self.conn, [(code, f"{market}.{code}", name, market,
                                          "2000-01-01", 0)])


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_ROOT", root)
    conn = meta.init(tmp_path / "meta.db")
    try:
        yield Env(root, conn)
    finally:
        conn.close()
