"""Task 23：回测基准（指数）的查找。

指数的键与个股不同：`000300` 这只指数在不同数据源里市场归属不一致
（`sh.000300` / `sz.000300` / 裸 `000300` 都有人用），所以查找要依次试。
但**落库键必须唯一**：一旦 `sh.000300` 与 `sz.000300` 指向同一个文件，
回测就会拿着另一只指数的收益去当基准 —— 那比缺基准更糟（缺基准至少会写明）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from chanlun.backtest import runner
from chanlun.data import store


def _bench(start: float = 4000.0, n: int = 5) -> pd.DataFrame:
    ts = pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame({"ts": ts, "open": start, "high": start + 10, "low": start - 10,
                         "close": start + 5, "volume": 1e6, "amount": 4e9})


def test_path_for_strips_market_prefix():
    """文件名不含市场前缀（市场由父目录表达），否则目录里会再写一遍市场。"""
    assert store.path_for("sh.000300", "day").as_posix().endswith("day/sh/000300.parquet")


def test_prefixed_index_and_same_bare_code_do_not_share_a_file():
    store.write("sh.000001", "day", _bench(n=5))   # 上证指数
    store.write("000001", "day", _bench(n=7))      # 平安银行（深市裸码）
    assert len(store.read("sh.000001", "day")) == 5
    assert len(store.read("000001", "day")) == 7
    assert store.path_for("sh.000001", "day") != store.path_for("000001", "day")


def test_benchmark_lookup_finds_prefixed_index():
    store.write("sh.000300", "day", _bench())
    code, df = runner._read_benchmark("000300", "day", None, None)
    assert code == "sh.000300"
    assert len(df) == 5


def test_benchmark_lookup_prefers_bare_key_when_it_exists():
    store.write("000300", "day", _bench(n=3))
    code, df = runner._read_benchmark("000300", "day", None, None)
    assert code == "000300" and len(df) == 3


def test_benchmark_lookup_returns_none_when_absent():
    assert runner._read_benchmark("000300", "day", None, None) == (None, None)


def test_sync_key_keeps_prefix_for_index():
    """同步落库键：个股去前缀（与一期一致），指数必须带前缀。"""
    from chanlun.data import markets
    from chanlun.data.baostock_source import to_bs_code

    assert markets.store_key(to_bs_code("sh.000300")) == "sh.000300"
    assert markets.store_key(to_bs_code("sh.600000")) == "600000"
