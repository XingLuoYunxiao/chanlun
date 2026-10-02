"""Web 测试共用夹具。

`cfg` 把数据根指向临时目录并写入 4 只样本日线；`client` 起一个 TestClient。

**`_CACHE.clear()` 是必需的**：`api.py:70` 的 `_CACHE` 是**模块级**的，
跨测试函数、跨 TestClient 实例都共享，且**现有测试没有一个清它**。
不清的话新写的缓存键测试可能读到别的用例留下的快照而假绿。

**五个既有测试文件（`test_api.py` / `test_api_adjust_ma.py` /
`test_api_periods_watchlist.py` / `test_api_sync.py` / `test_structure_window.py`）
里的同名重复夹具一律不动**：模块级夹具会**遮蔽**本文件的同名夹具，
所以它们的行为**零变化**；删掉它们是纯机械清理，只会把这个任务的 diff 撑大、
让评审去重定位无关代码。

`test_frontend_sync_button.py` / `test_frontend_watchlist.py` 不请求这两个夹具
（它们只读 `web/static/`），因此本文件的加入对它们同样无副作用。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from chanlun.config import load_config
from chanlun.data import meta, store
from chanlun.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "tests" / "chan" / "fixtures" / "bars.parquet"
CODES = ("sh.600000", "sh.601088", "sz.300059", "sz.300750")


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """把数据根指向临时目录，并写入 4 只样本的日线（代码与真实同步一致：裸数字）。"""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    # DataConfig.meta_db 是 root 下的派生属性（只读），不能当字段替换
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))
    df = pd.read_parquet(BARS)
    for bs_code, g in df.groupby("code"):
        store.upsert(str(bs_code).split(".")[1], "day", g.drop(columns=["code"]))
    conn = meta.init(conf.data.meta_db)
    meta.upsert_universe(conn, [(c.split(".")[1], c, "样本", c.split(".")[0], "", 0) for c in CODES])
    conn.close()
    # `_CACHE` 是模块级、跨测试共享的；不清的话缓存键测试会读到别的用例留下的快照。
    from chanlun.web import api as web_api

    with web_api._CACHE_LOCK:
        web_api._CACHE.clear()
    return conf


@pytest.fixture()
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c
