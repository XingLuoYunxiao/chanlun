"""全局测试隔离：任何测试都不许写进真实 ``data/`` 目录。

教训（真实事故）：`store.DATA_ROOT` 是**模块级全局，不是环境变量**。曾有一组测试用
``monkeypatch.setenv("CHANLUN_DATA_ROOT", ...)`` 以为隔离了，结果直接覆写了生产
``data/day/sh/600000.parquet``、``data/day/sz/000001.parquet``、沪深300 基准文件。
`meta.db` 同理，缺省路径由 ``data.root`` 派生。这里对两者统一兜底；
个别测试要指到别处，自己的 monkeypatch 会覆盖本夹具。
"""

from __future__ import annotations

import pytest

from chanlun.config import Config, DataConfig
from chanlun.data import meta, store


@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    cfg = Config(data=DataConfig(root=tmp_path / "data"), log_path=tmp_path / "chanlun.log")
    monkeypatch.setattr(store, "DATA_ROOT", cfg.data.root)
    monkeypatch.setattr(meta, "load_config", lambda *a, **k: cfg)
    assert store.data_root() == cfg.data.root
    assert store.path_for("600000", "day").is_relative_to(tmp_path)
    assert cfg.data.meta_db.is_relative_to(tmp_path)
    return cfg


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """**默认禁网**，让「偷偷联网」的测试**大声失败**，而不是拖慢或超时。

    为什么需要它（2026-10-15 实测）：
    `pyproject.toml` 写着 `addopts = "-m 'not live'"`，README 也说「联网测试默认不跑」，
    但**没有任何东西真的挡住连接** —— 只要某个测试不小心走到取数路径，它就会真联网。

    这条漏网会以**极难定位的方式**爆出来：`web/api.py` 的 `_SYNC_RUN_LOCK` 把
    同步任务**全局串行化**，于是一个正在做真实请求的后台任务会一直占着锁；
    后面某个测试的同步任务只能干等，它的 5 秒轮询窗口一过就报
    「同步一直没结束」——**报错的地方离肇事的地方隔了好几个文件**。

    实测触发链：`/api/structure` 走到 D-49「按需补最新」→ 起后台同步 → 真联网。

    **要联网的测试**：给它打上 `@pytest.mark.live`（那类默认被 `-m 'not live'` 排除），
    或者自己 monkeypatch 掉 `_fetch_one` / 源的 HTTP 函数 —— 后者是绝大多数测试
    已经在做的事。本机回环（`127.0.0.1`）放行，`TestClient` 不走 socket 也不受影响。
    """
    if request.node.get_closest_marker("live"):
        return
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(
                f"测试不许联网（试图连 {host}）。"
                f"要真的联网请打 @pytest.mark.live，"
                f"否则请 monkeypatch 掉取数函数。"
            )
        return real_connect(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded, raising=False)
