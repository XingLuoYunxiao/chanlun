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
