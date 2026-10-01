"""兜底：测试期间 `store` 必须指向临时目录。

教训：`store.DATA_ROOT` 是模块级全局，**不是环境变量**。曾经有一组测试用
`monkeypatch.setenv("CHANLUN_DATA_ROOT", ...)` 以为隔离了，结果直接覆写了生产
`data/day/sh/600000.parquet` 等文件。这条测试和 `tests/conftest.py` 的自动夹具一起，
保证同类失误不会再落到真实数据上。
"""

from __future__ import annotations

from chanlun.config import load_config
from chanlun.data import store


def test_store_data_root_is_isolated():
    assert store.data_root() != load_config().data.root, "测试正在用生产数据目录"


def test_store_paths_stay_inside_tmp(tmp_path):
    assert store.path_for("600000", "day").as_posix().startswith(tmp_path.as_posix())
