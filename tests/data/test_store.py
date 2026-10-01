from pathlib import Path

import pandas as pd
import pytest

from chanlun.data import store
from chanlun.data.types import COLUMNS, normalize


def _df(n: int = 30, start: str = "2024-01-02") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [1.0 + i for i in range(n)],
            "high": [2.0 + i for i in range(n)],
            "low": [0.5 + i for i in range(n)],
            "close": [1.5 + i for i in range(n)],
            "volume": [10.0] * n,
            "amount": [100.0] * n,
        }
    )


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")


def test_upsert_then_read_roundtrip():
    df = _df(30)
    assert store.upsert("600000", "day", df) == 30
    back = store.read("600000", "day")
    assert list(back.columns) == COLUMNS
    assert len(back) == 30
    assert back["ts"].is_unique
    assert float(back["close"].iloc[-1]) == float(df["close"].iloc[-1])


def test_upsert_is_idempotent():
    df = _df(30)
    store.upsert("600000", "day", df)
    assert store.upsert("600000", "day", df) == 0
    assert store.row_count("600000", "day") == 30


def test_upsert_appends_only_new_rows():
    store.upsert("600000", "day", _df(30))
    more = _df(40).tail(15)  # 与已有 30 行重叠 5 行，新增 10 行
    added = store.upsert("600000", "day", more)
    assert added == 10
    assert store.row_count("600000", "day") == 40
    assert store.last_ts("600000", "day") == str(more["ts"].iloc[-1])


def test_path_layout_includes_market():
    store.upsert("600000", "day", _df(3))
    store.upsert("000001", "30", _df(3))
    store.upsert("830799", "day", _df(3))
    assert store.path_for("600000", "day").as_posix().endswith("day/sh/600000.parquet")
    assert store.path_for("000001", "30").as_posix().endswith("30/sz/000001.parquet")
    assert store.path_for("830799", "day").as_posix().endswith("day/bj/830799.parquet")


def test_read_truncation_is_point_in_time():
    df = _df(30)
    store.upsert("600000", "day", df)
    cut = str(df["ts"].iloc[19])
    back = store.read("600000", "day", end=cut)
    assert len(back) == 20
    assert back["ts"].iloc[-1] == cut


def test_read_limit_and_start():
    store.upsert("600000", "day", _df(30))
    assert len(store.read("600000", "day", limit=5)) == 5
    assert len(store.read("600000", "day", start="2024-01-10")) < 30


def test_read_missing_file_returns_empty_frame():
    out = store.read("999999", "day")
    assert len(out) == 0
    assert list(out.columns) == COLUMNS


def test_normalize_dedups_and_sorts():
    df = pd.concat([_df(5), _df(5)], ignore_index=True)  # 完全重复
    out = normalize(df)
    assert len(out) == 5
    assert out["ts"].is_monotonic_increasing


def test_market_of_handles_prefixed_code():
    assert store.market_of("sh.600000") == "sh"
    assert store.market_of("600000") == "sh"


def test_write_is_atomic_so_a_concurrent_read_never_sees_a_half_file(tmp_path, monkeypatch):
    """同步（分钟～小时级）与看盘页读盘是并发的，写盘期间读盘只能读到「完整的旧内容」。

    真实事故形态：`write` 直接覆写目标文件，读盘方读到写了一半的 parquet，pyarrow 抛
    `Parquet magic bytes not found` —— 页面直接 500，而这跟缠论逻辑一点关系都没有。

    这里不去赛跑（赛跑可能跑不出来），而是**强制**制造那个时刻：拦截 `to_parquet`，
    落盘后立刻把该文件截断一半（等价于「写到一半」），在这个状态下调一次 `store.read`。
    """
    store.write("600000", "day", _df(30))  # 旧内容 30 行
    seen: dict[str, object] = {}
    real_to_parquet = pd.DataFrame.to_parquet

    def interrupted_write(self, path, *args, **kwargs):
        real_to_parquet(self, path, *args, **kwargs)
        target = Path(path)
        raw = target.read_bytes()
        target.write_bytes(raw[: len(raw) // 2])  # 砍一半 = 写盘写到一半的样子
        try:
            seen["rows"] = len(store.read("600000", "day"))
        except Exception as exc:  # noqa: BLE001
            seen["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            target.write_bytes(raw)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", interrupted_write)
    store.write("600000", "day", _df(40))

    assert "error" not in seen, f"读盘方读到了写了一半的文件：{seen['error']}"
    assert seen["rows"] == 30, "读盘方应读到完整的旧内容（30 行）"
    assert len(store.read("600000", "day")) == 40, "写完必须是完整的新内容"
    assert not list((tmp_path / "data").rglob("*.tmp")), "不允许留下临时文件"
