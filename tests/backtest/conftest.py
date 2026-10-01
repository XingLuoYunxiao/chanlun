"""Task 14 回测测试的公共夹具：全部离线、只写临时目录。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from chanlun.data import store

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 让 `import _synth` 稳定可用


@pytest.fixture()
def seed_store(tmp_path, monkeypatch):
    """把 `store.DATA_ROOT` 指到临时目录，返回写样本数据的函数。"""
    root: Path = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)

    def _seed(code: str, df: pd.DataFrame, period: str = "day") -> Path:
        return store.write(code, period, df)

    _seed.root = root  # type: ignore[attr-defined]
    return _seed
